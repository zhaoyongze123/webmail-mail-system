from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response, status
from pydantic import BaseModel, EmailStr

from app.cache import LoginFailureLimiter, SessionStore
from app.config import Settings, get_settings
from app.crypto import decrypt_text, encrypt_text
from app.errors import AppError
from app.mail_adapters import ImapAdapter, ImapSettings, MailAdapterError
from app.redis_client import get_redis_client


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember: bool = False


@dataclass(frozen=True)
class AuthSession:
    session_id: str
    email: str
    password: str
    imap: dict[str, object]
    smtp: dict[str, object]


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _imap_settings(email: str, password: str, settings: Settings) -> ImapSettings:
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


def login_user(request: Request, payload: LoginRequest) -> tuple[str, dict[str, object]]:
    settings = get_settings()
    redis_client = get_redis_client()
    limiter = LoginFailureLimiter(client=redis_client, settings=settings)
    ip = _client_ip(request)
    email = payload.email.lower()

    if limiter.is_limited(ip, email):
        raise AppError(
            "AUTH_RATE_LIMITED",
            "登录失败次数过多，请稍后再试",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        authenticate_mailbox(email, payload.password, settings)
    except AppError:
        failures = limiter.record_failure(ip, email)
        if failures >= settings.login_fail_limit:
            raise AppError(
                "AUTH_RATE_LIMITED",
                "登录失败次数过多，请稍后再试",
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        raise

    limiter.clear(ip, email)
    session_store = SessionStore(client=redis_client, settings=settings)
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
            "secret": encrypt_text(payload.password),
        }
    )
    return session_id, {"email": email}


def set_session_cookie(response: Response, session_id: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )


def get_current_session(request: Request) -> AuthSession:
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
    )


def logout_user(request: Request) -> None:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        SessionStore(client=get_redis_client(), settings=settings).delete(session_id)
