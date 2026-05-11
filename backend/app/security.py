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
    return secrets.token_urlsafe(32)


def set_cookie(response: Response, name: str, value: str, *, max_age: int | None = None, secure: bool | None = None) -> None:
    settings = app_config.get_settings()
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=name != CSRF_COOKIE_NAME,
        secure=settings.session_cookie_secure if secure is None else secure,
        samesite="lax",
        path="/",
    )


def clear_cookie(response: Response, name: str, *, secure: bool | None = None) -> None:
    settings = app_config.get_settings()
    response.delete_cookie(
        name,
        secure=settings.session_cookie_secure if secure is None else secure,
        samesite="lax",
        path="/",
    )


def issue_session_cookies(response: Response, session_id: str, csrf_token: str) -> None:
    settings = app_config.get_settings()
    set_cookie(response, settings.session_cookie_name, session_id, max_age=settings.session_ttl_seconds)
    set_cookie(response, CSRF_COOKIE_NAME, csrf_token, secure=settings.session_cookie_secure)


def clear_session_cookies(response: Response) -> None:
    settings = app_config.get_settings()
    clear_cookie(response, settings.session_cookie_name)
    clear_cookie(response, CSRF_COOKIE_NAME)


def is_safe_attachment_id(attachment_id: str) -> bool:
    candidate = attachment_id.strip()
    if not candidate or "\x00" in candidate or "\\" in candidate:
        return False
    if candidate.startswith("/") or candidate.endswith("/"):
        return False
    if ".." in candidate:
        return False
    return bool(_PATH_SEGMENT_RE.fullmatch(candidate))


def validate_attachment_id(attachment_id: str) -> None:
    if not is_safe_attachment_id(attachment_id):
        raise AppError(
            "ATTACHMENT_INVALID_ID",
            "附件标识不合法",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


def _load_session_csrf_token(request: Request) -> str | None:
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
    if request.method in SAFE_METHODS:
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
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def csrf_token_from_request(request: Request) -> str | None:
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
    logger.info("%s %s", message, json.dumps(sanitize_log_value(context), ensure_ascii=False, sort_keys=True))
