"""安全相关工具：CSRF、Cookie、附件标识与日志脱敏。"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import Request, Response, status

from app import config as app_config
from app import redis_client as redis_client_module
from app.cache import SessionStore
from app.errors import AppError


logger = logging.getLogger("app.security")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_COOKIE_NAME = "webmail_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._@+\- ]+(?:/[A-Za-z0-9._@+\- ]+)*$")
_SENSITIVE_KEYS = {
    "authorization",
    "body",
    "content",
    "content_b64",
    "cookie",
    "cookies",
    "csrf",
    "html_body",
    "password",
    "secret",
    "set_cookie",
    "text_body",
    "token",
}


def new_csrf_token() -> str:
    """生成新的 CSRF 令牌。"""
    return secrets.token_urlsafe(32)


def _request_uses_https(request: Request | None) -> bool:
    """判断当前请求链路是否处于 HTTPS。"""
    if request is None:
        return False
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme.lower() == "https"


def _resolve_cookie_secure(request: Request | None, secure: bool | None = None) -> bool:
    """基于配置和当前请求链路决定是否写入 Secure Cookie。"""
    settings = app_config.get_settings()
    if secure is not None:
        return secure
    if not settings.session_cookie_secure:
        return False
    return _request_uses_https(request)


def set_cookie(
    response: Response,
    name: str,
    value: str,
    *,
    request: Request | None = None,
    max_age: int | None = None,
    secure: bool | None = None,
) -> None:
    """按系统安全策略写入 Cookie。"""
    settings = app_config.get_settings()
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=name != CSRF_COOKIE_NAME,
        secure=_resolve_cookie_secure(request, secure),
        samesite="lax",
        path="/",
    )


def clear_cookie(response: Response, name: str, *, request: Request | None = None, secure: bool | None = None) -> None:
    """按系统安全策略清除 Cookie。"""
    response.delete_cookie(
        name,
        secure=_resolve_cookie_secure(request, secure),
        samesite="lax",
        path="/",
    )


def issue_session_cookies(response: Response, session_id: str, csrf_token: str, *, request: Request | None = None) -> None:
    """同时写入会话 Cookie 与 CSRF Cookie。"""
    settings = app_config.get_settings()
    set_cookie(
        response,
        settings.session_cookie_name,
        session_id,
        request=request,
        max_age=settings.session_ttl_seconds,
    )
    set_cookie(response, CSRF_COOKIE_NAME, csrf_token, request=request)


def clear_session_cookies(response: Response, *, request: Request | None = None) -> None:
    """同时清除会话 Cookie 与 CSRF Cookie。"""
    settings = app_config.get_settings()
    clear_cookie(response, settings.session_cookie_name, request=request)
    clear_cookie(response, CSRF_COOKIE_NAME, request=request)


def is_safe_attachment_id(attachment_id: str) -> bool:
    """判断附件标识是否满足路径安全约束。"""
    candidate = attachment_id.strip()
    if not candidate or "\x00" in candidate or "\\" in candidate:
        return False
    if candidate.startswith("/") or candidate.endswith("/"):
        return False
    if ".." in candidate:
        return False
    return bool(_PATH_SEGMENT_RE.fullmatch(candidate))


def validate_attachment_id(attachment_id: str) -> None:
    """校验附件标识，不合法时抛出业务异常。"""
    if not is_safe_attachment_id(attachment_id):
        raise AppError(
            "ATTACHMENT_INVALID_ID",
            "附件标识不合法",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


def _load_session_csrf_token(request: Request) -> str | None:
    """从 Redis 会话中读取已签发的 CSRF 令牌。"""
    settings = app_config.get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    session_data = SessionStore(client=redis_client_module.get_redis_client(), settings=settings).get(session_id)
    if not session_data:
        return None
    token = session_data.get("csrf_token")
    return str(token) if token else None


def validate_csrf_request(request: Request) -> None:
    """对需要保护的写请求执行 CSRF 校验。"""
    if request.method in SAFE_METHODS:
        return
    if request.url.path.startswith("/api/admin/"):
        return
    if request.url.path in {"/api/auth/login", "/api/auth/register"}:
        return
    expected_token = _load_session_csrf_token(request)
    if not expected_token:
        return

    provided_token = request.headers.get(CSRF_HEADER_NAME)
    if not provided_token or not secrets.compare_digest(expected_token, provided_token):
        raise AppError(
            "CSRF_TOKEN_INVALID",
            "请求校验失败，请刷新后重试",
            http_status=status.HTTP_403_FORBIDDEN,
        )


def add_security_headers(response: Response) -> Response:
    """为响应追加默认安全响应头。"""
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def csrf_token_from_request(request: Request) -> str | None:
    """从请求上下文关联的会话中提取 CSRF 令牌。"""
    settings = app_config.get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    session_data = SessionStore(client=redis_client_module.get_redis_client(), settings=settings).get(session_id)
    if not session_data:
        return None
    token = session_data.get("csrf_token")
    return str(token) if token else None


def sanitize_log_value(value: Any) -> Any:
    """递归脱敏日志中的敏感字段。"""
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in _SENSITIVE_KEYS or "password" in key_text or "cookie" in key_text or key_text.endswith("body"):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = sanitize_log_value(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_log_value(item) for item in value)
    if isinstance(value, (bytes, bytearray)):
        return "[BINARY]"
    return value


def log_sanitized_event(message: str, **context: Any) -> None:
    """记录已经脱敏的结构化日志。"""
    logger.info("%s %s", message, json.dumps(sanitize_log_value(context), ensure_ascii=False, sort_keys=True))
