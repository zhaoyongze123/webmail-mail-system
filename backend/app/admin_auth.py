from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pyotp
from fastapi import Depends, Header, Request, status
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_common import AdminContext, cleanup_refresh_tokens, ensure_active_admin, get_db_session, normalize_utc, utcnow
from app.config import get_settings
from app.errors import AppError
from app.models import AdminRefreshToken, AdminUser
from app.observability import record_audit_event


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
ADMIN_BEARER_PREFIX = "Bearer "


class AdminLoginRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, min_length=6, max_length=10)


class AdminRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class AdminChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AdminTotpConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def _password_policy(password: str) -> None:
    has_upper = any(char.isupper() for char in password)
    has_lower = any(char.islower() for char in password)
    has_digit = any(char.isdigit() for char in password)
    has_symbol = any(not char.isalnum() for char in password)
    if len(password) < 8 or sum([has_upper, has_lower, has_digit, has_symbol]) < 3:
        raise AppError(
            "ADMIN_PASSWORD_WEAK",
            "密码必须至少 8 位，并包含大写、小写、数字、符号中的至少三类",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


def hash_password(password: str) -> str:
    _password_policy(password)
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _encode_access_token(user: AdminUser) -> tuple[str, datetime]:
    settings = get_settings()
    expires_at = utcnow() + timedelta(minutes=settings.admin_access_token_ttl_minutes)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "domain_id": str(user.domain_id) if user.domain_id else None,
        "exp": expires_at,
        "type": "admin_access",
    }
    token = jwt.encode(payload, settings.effective_admin_jwt_secret, algorithm="HS256")
    return token, expires_at


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_refresh_token(db: Session, user: AdminUser) -> tuple[str, datetime]:
    expires_at = utcnow() + timedelta(days=get_settings().admin_refresh_token_ttl_days)
    token = secrets.token_urlsafe(48)
    db.add(
        AdminRefreshToken(
            admin_user_id=user.id,
            token_hash=_hash_refresh_token(token),
            expires_at=expires_at,
        )
    )
    return token, expires_at


def issue_token_bundle(db: Session, user: AdminUser) -> TokenBundle:
    cleanup_refresh_tokens(db, user_id=user.id)
    access_token, access_expires_at = _encode_access_token(user)
    refresh_token, refresh_expires_at = _issue_refresh_token(db, user)
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_settings().effective_admin_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AppError(
            "ADMIN_AUTH_INVALID",
            "后台登录已失效，请重新登录",
            http_status=status.HTTP_401_UNAUTHORIZED,
        ) from exc
    if payload.get("type") != "admin_access":
        raise AppError(
            "ADMIN_AUTH_INVALID",
            "后台登录已失效，请重新登录",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    return payload


def bootstrap_admin_user(db: Session) -> None:
    settings = get_settings()
    username = settings.effective_admin_bootstrap_username
    password = settings.effective_admin_bootstrap_password
    if not username or not password:
        return
    existing = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if existing is not None:
        return
    db.add(
        AdminUser(
            username=username,
            password_hash=hash_password(password),
            role="superadmin",
            is_active=True,
        )
    )
    db.flush()


def authenticate_admin(request: Request, payload: AdminLoginRequest, db: Session) -> tuple[AdminUser, TokenBundle]:
    bootstrap_admin_user(db)
    identity = (payload.username or payload.email or "").strip()
    user = db.scalar(select(AdminUser).where(AdminUser.username == identity))
    user = ensure_active_admin(user)
    if not verify_password(payload.password, user.password_hash):
        record_audit_event(
            request,
            "admin.auth.login",
            success=False,
            actor_type="admin_user",
            actor_id=str(user.id),
            metadata={"username": identity, "reason": "invalid_credentials"},
        )
        raise AppError(
            "ADMIN_AUTH_INVALID",
            "用户名或密码错误",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    if user.totp_enabled:
        if not payload.totp_code:
            raise AppError(
                "ADMIN_TOTP_REQUIRED",
                "请输入二次验证码",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
        totp = pyotp.TOTP(user.totp_secret or "")
        if not totp.verify(payload.totp_code, valid_window=1):
            raise AppError(
                "ADMIN_TOTP_INVALID",
                "二次验证码错误",
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
    user.last_login_at = utcnow()
    bundle = issue_token_bundle(db, user)
    record_audit_event(
        request,
        "admin.auth.login",
        success=True,
        actor_type="admin_user",
        actor_id=str(user.id),
        metadata={"username": user.username, "role": user.role},
    )
    return user, bundle


def refresh_admin_token(request: Request, payload: AdminRefreshRequest, db: Session) -> tuple[AdminUser, TokenBundle]:
    token_hash = _hash_refresh_token(payload.refresh_token)
    record = db.scalar(select(AdminRefreshToken).where(AdminRefreshToken.token_hash == token_hash))
    if record is None or record.revoked_at is not None or normalize_utc(record.expires_at) <= utcnow():
        raise AppError(
            "ADMIN_REFRESH_INVALID",
            "刷新令牌无效或已过期",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    user = ensure_active_admin(db.get(AdminUser, record.admin_user_id))
    record.revoked_at = utcnow()
    bundle = issue_token_bundle(db, user)
    record_audit_event(
        request,
        "admin.auth.refresh",
        success=True,
        actor_type="admin_user",
        actor_id=str(user.id),
        metadata={"username": user.username},
    )
    return user, bundle


def revoke_refresh_token(db: Session, token: str) -> None:
    token_hash = _hash_refresh_token(token)
    record = db.scalar(select(AdminRefreshToken).where(AdminRefreshToken.token_hash == token_hash))
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow()


def build_admin_context(user: AdminUser) -> AdminContext:
    return AdminContext(
        user_id=user.id,
        username=user.username,
        role=user.role,
        domain_id=user.domain_id,
    )


def get_current_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db_session),
) -> AdminContext:
    bootstrap_admin_user(db)
    if not authorization or not authorization.startswith(ADMIN_BEARER_PREFIX):
        raise AppError(
            "ADMIN_AUTH_INVALID",
            "后台登录已失效，请重新登录",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    payload = decode_access_token(authorization[len(ADMIN_BEARER_PREFIX) :].strip())
    user_id = payload.get("sub")
    if not user_id:
        raise AppError(
            "ADMIN_AUTH_INVALID",
            "后台登录已失效，请重新登录",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    user = ensure_active_admin(db.get(AdminUser, UUID(str(user_id))))
    return build_admin_context(user)


def build_auth_payload(user: AdminUser, bundle: TokenBundle) -> dict[str, Any]:
    return {
        "user": {
            "id": str(user.id),
            "email": user.username,
            "name": user.username,
            "username": user.username,
            "role": user.role,
            "domain_id": str(user.domain_id) if user.domain_id else None,
            "totp_enabled": user.totp_enabled,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        },
        "access_token": bundle.access_token,
        "refresh_token": bundle.refresh_token,
        "expires_at": bundle.access_expires_at.isoformat(),
        "refresh_expires_at": bundle.refresh_expires_at.isoformat(),
    }
