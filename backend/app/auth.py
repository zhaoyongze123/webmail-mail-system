"""邮箱登录、会话持久化和凭据同步相关逻辑。

这个模块负责普通邮箱账号的登录/注册、Redis 会话管理、CSRF 令牌和
密码更新后的会话同步，供前后端认证流程复用。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response, status
from pydantic import BaseModel, EmailStr

from app.cache import LoginFailureLimiter, SessionStore
from app.config import Settings, get_settings
from app.crypto import decrypt_text, encrypt_text
from app.errors import AppError
from app.mail_adapters import ImapAdapter, ImapSettings, MailAdapterError
from app.mail_preferences import get_user_preferences
from app.mail_state import ensure_mail_account
from app.redis_client import get_redis_client
from app.security import issue_session_cookies, new_csrf_token, clear_session_cookies
from app.observability import record_audit_event


class LoginRequest(BaseModel):
    """邮箱登录请求体。"""
    email: EmailStr
    password: str
    remember: bool = False


class RegisterRequest(LoginRequest):
    """邮箱注册请求体，沿用登录字段并补充昵称。"""
    display_name: str | None = None


@dataclass(frozen=True)
class AuthSession:
    """当前已登录邮箱会话的运行时视图。"""
    session_id: str
    email: str
    password: str
    imap: dict[str, object]
    smtp: dict[str, object]
    preferences: dict[str, object]


def _client_ip(request: Request) -> str:
    """提取请求来源 IP，用于登录失败限流和审计。"""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _imap_settings(email: str, password: str, settings: Settings) -> ImapSettings:
    """构造 IMAP 连接参数。"""
    return ImapSettings(
        host=settings.mail_imap_host,
        port=settings.mail_imap_port,
        username=email,
        password=password,
        use_ssl=settings.mail_imap_ssl,
        starttls=settings.mail_imap_starttls,
        timeout=15,
    )


def authenticate_mailbox(email: str, password: str, settings: Settings | None = None) -> None:
    """通过 IMAP 直连验证邮箱账号和密码是否有效。"""
    settings = settings or get_settings()
    adapter = ImapAdapter(_imap_settings(email, password, settings))
    try:
        adapter.connect().login()
    except MailAdapterError as exc:
        raise AppError(
            "AUTH_INVALID_CREDENTIALS",
            "邮箱或密码不正确",
            http_status=status.HTTP_401_UNAUTHORIZED,
        ) from exc
    finally:
        try:
            adapter.logout()
        except MailAdapterError:
            pass


def verify_mailbox_password(email: str, password: str, settings: Settings | None = None) -> None:
    """复用邮箱认证逻辑校验密码。"""
    authenticate_mailbox(email, password, settings)


def _create_session(email: str, password: str, settings: Settings) -> tuple[str, dict[str, object], str]:
    """创建 Redis 会话并生成对应 CSRF 令牌。"""
    session_store = SessionStore(client=get_redis_client(), settings=settings)
    csrf_token = new_csrf_token()
    session_id = session_store.create(
        {
            "email": email,
            "imap": {
                "host": settings.mail_imap_host,
                "port": settings.mail_imap_port,
                "ssl": settings.mail_imap_ssl,
                "starttls": settings.mail_imap_starttls,
            },
            "smtp": {
                "host": settings.mail_smtp_host,
                "port": settings.mail_smtp_port,
                "ssl": settings.mail_smtp_ssl,
                "starttls": settings.mail_smtp_starttls,
            },
            "secret": encrypt_text(password),
            "csrf_token": csrf_token,
        }
    )
    return session_id, {"email": email}, csrf_token


def login_user(request: Request, payload: LoginRequest) -> tuple[str, dict[str, object], str]:
    """处理邮箱登录，含限流、认证、会话创建和审计记录。"""
    settings = get_settings()
    redis_client = get_redis_client()
    limiter = LoginFailureLimiter(client=redis_client, settings=settings)
    ip = _client_ip(request)
    email = payload.email.lower()

    if limiter.is_limited(ip, email):
        record_audit_event(
            request,
            "auth.login.rate_limited",
            success=False,
            metadata={"email": email, "ip": ip},
        )
        raise AppError(
            "AUTH_RATE_LIMITED",
            "登录失败次数过多，请稍后再试",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        authenticate_mailbox(email, payload.password, settings)
    except AppError:
        record_audit_event(
            request,
            "auth.login",
            success=False,
            metadata={"email": email, "reason": "invalid_credentials"},
        )
        failures = limiter.record_failure(ip, email)
        if failures >= settings.login_fail_limit:
            record_audit_event(
                request,
                "auth.login.rate_limited",
                success=False,
                metadata={"email": email, "ip": ip},
            )
            raise AppError(
                "AUTH_RATE_LIMITED",
                "登录失败次数过多，请稍后再试",
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        raise

    limiter.clear(ip, email)
    session_id, user_data, csrf_token = _create_session(email, payload.password, settings)
    ensure_mail_account(email)
    record_audit_event(
        request,
        "auth.login",
        success=True,
        metadata={"email": email, "remember": payload.remember},
    )
    return session_id, user_data, csrf_token


def register_user(request: Request, payload: RegisterRequest) -> tuple[str, dict[str, object], str]:
    """处理邮箱注册并创建登录会话。"""
    settings = get_settings()
    email = payload.email.lower()
    try:
        authenticate_mailbox(email, payload.password, settings)
    except AppError:
        record_audit_event(
            request,
            "auth.register",
            success=False,
            metadata={"email": email, "reason": "invalid_credentials"},
        )
        raise
    ensure_mail_account(email, display_name=payload.display_name)
    session_id, user_data, csrf_token = _create_session(email, payload.password, settings)
    record_audit_event(
        request,
        "auth.register",
        success=True,
        metadata={"email": email, "has_display_name": bool(payload.display_name)},
    )
    return session_id, user_data, csrf_token


def set_session_cookie(response: Response, session_id: str, csrf_token: str) -> None:
    """把登录会话写入响应 Cookie。"""
    issue_session_cookies(response, session_id, csrf_token)


def clear_session_cookie(response: Response) -> None:
    """清理登录会话 Cookie。"""
    clear_session_cookies(response)


def get_current_session(request: Request) -> AuthSession:
    """从请求中恢复当前邮箱会话。"""
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise AppError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", http_status=status.HTTP_401_UNAUTHORIZED)

    session_store = SessionStore(client=get_redis_client(), settings=settings)
    session_data = session_store.get(session_id)
    if not session_data:
        raise AppError("AUTH_SESSION_EXPIRED", "登录已过期，请重新登录", http_status=status.HTTP_401_UNAUTHORIZED)

    session_store.refresh(session_id)
    return AuthSession(
        session_id=session_id,
        email=str(session_data["email"]),
        password=decrypt_text(str(session_data["secret"])),
        imap=dict(session_data.get("imap") or {}),
        smtp=dict(session_data.get("smtp") or {}),
        preferences=get_user_preferences(str(session_data["email"])),
    )


def update_session_password(session_id: str, password: str, settings: Settings | None = None) -> None:
    """在不重建会话的情况下同步更新会话内保存的邮箱密码。"""
    settings = settings or get_settings()
    SessionStore(client=get_redis_client(), settings=settings).update(
        session_id,
        {"secret": encrypt_text(password)},
    )


def logout_user(request: Request) -> None:
    """注销当前邮箱会话并记录审计事件。"""
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        SessionStore(client=get_redis_client(), settings=settings).delete(session_id)
    record_audit_event(
        request,
        "auth.logout",
        success=True,
        metadata={"session_present": bool(session_id)},
    )
