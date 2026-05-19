from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pyotp
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.admin_auth import (
    AdminChangePasswordRequest,
    AdminLoginRequest,
    AdminRefreshRequest,
    AdminTotpConfirmRequest,
    authenticate_admin,
    build_auth_payload,
    get_current_admin,
    hash_password,
    refresh_admin_token,
    revoke_refresh_token,
    verify_password,
)
from app.admin_common import (
    AdminContext,
    account_to_admin_dict,
    alias_to_dict,
    audit_log_to_dict,
    count_dashboard_metrics,
    domain_to_dict,
    ensure_domain_scope,
    ensure_superadmin,
    get_db_session,
    paginate,
    quota_policy_to_dict,
    record_admin_audit,
    normalize_pagination,
    utcnow,
)
from app.config import get_settings
from app.errors import AppError
from app.models import AdminUser, AuditLog, MailAccount, MailAlias, MailDomain, QuotaPolicy
from app.responses import success_response
from app.schemas import ApiResponse


router = APIRouter(prefix="/api/admin", tags=["admin"], responses={401: {"description": "未授权"}})


class BulkStatusRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, min_length=1)
    status: str = Field(pattern="^(active|disabled)$")


class DomainCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    quota_limit_mb: int = Field(default=10240, ge=1, le=1024 * 1024)
    status: str = Field(default="active", pattern="^(active|disabled)$")


class DomainUpdateRequest(BaseModel):
    quota_limit_mb: int | None = Field(default=None, ge=1, le=1024 * 1024)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class AdminUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=255)
    domain_id: str | None = None
    password: str = Field(min_length=8, max_length=256)
    quota_mb: int = Field(default=500, ge=1, le=1024 * 1024)
    status: str = Field(default="active", pattern="^(active|disabled)$")
    is_admin: bool = False


class AdminUserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    domain_id: str | None = None
    quota_mb: int | None = Field(default=None, ge=1, le=1024 * 1024)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    is_admin: bool | None = None


class AdminUserResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class AdminUsersBulkActionRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, min_length=1)
    action: str = Field(pattern="^(activate|disable|delete)$")


class AliasCreateRequest(BaseModel):
    domain_id: str
    source_address: EmailStr
    target_addresses: list[EmailStr] = Field(default_factory=list, min_length=1)


class AliasUpdateRequest(BaseModel):
    target_addresses: list[EmailStr] | None = None
    is_active: bool | None = None


class QuotaPolicyUpdateRequest(BaseModel):
    domain_id: str | None = None
    default_quota_mb: int = Field(ge=1, le=1024 * 1024)
    warn_80_enabled: bool = True
    warn_90_enabled: bool = True
    warn_95_enabled: bool = True


class UserQuotaUpdateRequest(BaseModel):
    quota_mb: int = Field(ge=1, le=1024 * 1024)


class QuotaBulkUpdateRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, min_length=1)
    quota_mb: int = Field(ge=1, le=1024 * 1024)


def _parse_uuid(value: str, *, code: str, message: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AppError(code, message, http_status=status.HTTP_400_BAD_REQUEST) from exc


def _normalize_domain_name(name: str) -> str:
    normalized = name.strip().lower()
    labels = normalized.split(".")
    if len(labels) < 2 or any(not label or len(label) > 63 for label in labels):
        raise AppError(
            "ADMIN_DOMAIN_INVALID",
            "域名格式不合法",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    for label in labels:
        if label.startswith("-") or label.endswith("-"):
            raise AppError(
                "ADMIN_DOMAIN_INVALID",
                "域名格式不合法",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
    return normalized


def _ensure_target_addresses(targets: list[str]) -> list[str]:
    normalized = [item.strip().lower() for item in targets if item.strip()]
    if not normalized:
        raise AppError(
            "ADMIN_ALIAS_TARGET_REQUIRED",
            "别名必须至少包含一个目标地址",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    return normalized


def _ensure_alias_not_conflict(db: Session, *, source_address: str, alias_id: UUID | None = None) -> None:
    if db.scalar(select(MailAccount).where(func.lower(MailAccount.email) == source_address.lower())):
        raise AppError(
            "ADMIN_ALIAS_CONFLICT",
            "别名地址与已有邮箱冲突",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    stmt = select(MailAlias).where(func.lower(MailAlias.source_address) == source_address.lower())
    if alias_id is not None:
        stmt = stmt.where(MailAlias.id != alias_id)
    if db.scalar(stmt):
        raise AppError(
            "ADMIN_ALIAS_CONFLICT",
            "别名地址与已有别名冲突",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


def _ensure_alias_no_direct_loop(source_address: str, target_addresses: list[str]) -> None:
    source = source_address.strip().lower()
    normalized_targets = [item.strip().lower() for item in target_addresses]
    if source in normalized_targets:
        raise AppError(
            "ADMIN_ALIAS_LOOP",
            "别名目标地址不能包含自身",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


@router.post("/auth/login", response_model=ApiResponse)
def admin_login(request: Request, payload: AdminLoginRequest, db: Session = Depends(get_db_session)) -> dict[str, Any]:
    user, bundle = authenticate_admin(request, payload, db)
    db.commit()
    return success_response(request, build_auth_payload(user, bundle))


@router.post("/auth/refresh", response_model=ApiResponse)
def admin_refresh(request: Request, payload: AdminRefreshRequest, db: Session = Depends(get_db_session)) -> dict[str, Any]:
    user, bundle = refresh_admin_token(request, payload, db)
    db.commit()
    return success_response(request, build_auth_payload(user, bundle))


@router.post("/auth/logout", response_model=ApiResponse)
def admin_logout(
    request: Request,
    payload: AdminRefreshRequest | None = None,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    if payload and payload.refresh_token:
        revoke_refresh_token(db, payload.refresh_token)
    record_admin_audit(request, admin, "admin.auth.logout", success=True)
    db.commit()
    return success_response(request, {"logged_out": True})


@router.get("/auth/me", response_model=ApiResponse)
def admin_me(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    user = db.get(AdminUser, admin.user_id)
    if user is None:
        raise AppError("ADMIN_AUTH_INVALID", "后台登录已失效，请重新登录", http_status=status.HTTP_401_UNAUTHORIZED)
    return success_response(
        request,
        {
            "id": str(user.id),
            "email": user.username,
            "name": user.username,
            "username": user.username,
            "role": user.role,
            "domain_id": str(user.domain_id) if user.domain_id else None,
            "totp_enabled": user.totp_enabled,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        },
    )


@router.post("/auth/change-password", response_model=ApiResponse)
def admin_change_password(
    request: Request,
    payload: AdminChangePasswordRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    user = db.get(AdminUser, admin.user_id)
    if user is None or not verify_password(payload.current_password, user.password_hash):
        raise AppError("ADMIN_AUTH_INVALID", "当前密码错误", http_status=status.HTTP_400_BAD_REQUEST)
    user.password_hash = hash_password(payload.new_password)
    record_admin_audit(request, admin, "admin.auth.change_password", success=True, target_type="admin_user", target_id=str(user.id))
    db.commit()
    return success_response(request, {"password_updated": True})


@router.post("/auth/totp/setup", response_model=ApiResponse)
def admin_totp_setup(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    user = db.get(AdminUser, admin.user_id)
    if user is None:
        raise AppError("ADMIN_AUTH_INVALID", "后台登录已失效，请重新登录", http_status=status.HTTP_401_UNAUTHORIZED)
    if not user.totp_secret:
        user.totp_secret = pyotp.random_base32()
    totp = pyotp.TOTP(user.totp_secret)
    provisioning_uri = totp.provisioning_uri(name=user.username, issuer_name=get_settings().admin_totp_issuer)
    db.commit()
    return success_response(
        request,
        {
            "secret": user.totp_secret,
            "provisioning_uri": provisioning_uri,
            "enabled": user.totp_enabled,
        },
    )


@router.post("/auth/totp/enable", response_model=ApiResponse)
def admin_totp_enable(
    request: Request,
    payload: AdminTotpConfirmRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    user = db.get(AdminUser, admin.user_id)
    if user is None or not user.totp_secret:
        raise AppError("ADMIN_TOTP_NOT_SETUP", "请先完成 TOTP 初始化", http_status=status.HTTP_400_BAD_REQUEST)
    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(payload.code, valid_window=1):
        raise AppError("ADMIN_TOTP_INVALID", "验证码错误", http_status=status.HTTP_400_BAD_REQUEST)
    user.totp_enabled = True
    record_admin_audit(request, admin, "admin.auth.totp_enable", success=True, target_type="admin_user", target_id=str(user.id))
    db.commit()
    return success_response(request, {"enabled": True})


@router.post("/auth/totp/disable", response_model=ApiResponse)
def admin_totp_disable(
    request: Request,
    payload: AdminTotpConfirmRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    user = db.get(AdminUser, admin.user_id)
    if user is None or not user.totp_secret:
        raise AppError("ADMIN_TOTP_NOT_SETUP", "尚未启用 TOTP", http_status=status.HTTP_400_BAD_REQUEST)
    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(payload.code, valid_window=1):
        raise AppError("ADMIN_TOTP_INVALID", "验证码错误", http_status=status.HTTP_400_BAD_REQUEST)
    user.totp_enabled = False
    record_admin_audit(request, admin, "admin.auth.totp_disable", success=True, target_type="admin_user", target_id=str(user.id))
    db.commit()
    return success_response(request, {"enabled": False})


@router.get("/domains", response_model=ApiResponse)
def list_domains(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = None,
    sort: str = Query(default="name"),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ensure_superadmin(admin)
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(MailDomain).options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
    if q:
        stmt = stmt.where(func.lower(MailDomain.name).contains(q.strip().lower()))
    if sort == "-created_at":
        stmt = stmt.order_by(MailDomain.created_at.desc())
    elif sort == "created_at":
        stmt = stmt.order_by(MailDomain.created_at.asc())
    else:
        stmt = stmt.order_by(MailDomain.name.asc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    domains = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[domain_to_dict(item) for item in domains]))


@router.post("/domains", response_model=ApiResponse)
def create_domain(
    request: Request,
    payload: DomainCreateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ensure_superadmin(admin)
    normalized = _normalize_domain_name(payload.name)
    if db.scalar(select(MailDomain).where(MailDomain.name == normalized)):
        raise AppError("ADMIN_DOMAIN_EXISTS", "域名已存在", http_status=status.HTTP_400_BAD_REQUEST)
    domain = MailDomain(name=normalized, quota_limit_mb=payload.quota_limit_mb, status=payload.status)
    db.add(domain)
    db.flush()
    record_admin_audit(request, admin, "admin.domains.create", success=True, target_type="domain", target_id=str(domain.id), metadata={"name": normalized})
    db.commit()
    db.refresh(domain)
    return success_response(request, {"domain": domain_to_dict(domain)})


@router.get("/domains/{domain_id}", response_model=ApiResponse)
def get_domain(
    request: Request,
    domain_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ensure_superadmin(admin)
    domain = db.scalar(
        select(MailDomain)
        .where(MailDomain.id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
        .options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
    )
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    return success_response(request, {"domain": domain_to_dict(domain)})


@router.patch("/domains/{domain_id}", response_model=ApiResponse)
def update_domain(
    request: Request,
    domain_id: str,
    payload: DomainUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ensure_superadmin(admin)
    domain = db.get(MailDomain, _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    if payload.quota_limit_mb is not None:
        domain.quota_limit_mb = payload.quota_limit_mb
    if payload.status is not None:
        domain.status = payload.status
    record_admin_audit(request, admin, "admin.domains.update", success=True, target_type="domain", target_id=str(domain.id))
    db.commit()
    db.refresh(domain)
    return success_response(request, {"domain": domain_to_dict(domain)})


@router.delete("/domains/{domain_id}", response_model=ApiResponse)
def delete_domain(
    request: Request,
    domain_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ensure_superadmin(admin)
    domain = db.scalar(
        select(MailDomain)
        .where(MailDomain.id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
        .options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
    )
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    impact = {"user_count": len(domain.accounts or []), "alias_count": len(domain.aliases or [])}
    db.delete(domain)
    record_admin_audit(request, admin, "admin.domains.delete", success=True, target_type="domain", target_id=domain_id, metadata=impact)
    db.commit()
    return success_response(request, {"deleted": True, "impact": impact})


@router.post("/domains/bulk-status", response_model=ApiResponse)
def bulk_domain_status(
    request: Request,
    payload: BulkStatusRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ensure_superadmin(admin)
    uuids = [_parse_uuid(item, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效") for item in payload.ids]
    domains = db.scalars(select(MailDomain).where(MailDomain.id.in_(uuids))).all()
    for domain in domains:
        domain.status = payload.status
    record_admin_audit(request, admin, "admin.domains.bulk_status", success=True, target_type="domain", metadata={"ids": payload.ids, "status": payload.status})
    db.commit()
    return success_response(request, {"updated": len(domains)})


@router.get("/users", response_model=ApiResponse)
def list_admin_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = None,
    domain_id: str | None = None,
    sort: str = Query(default="email"),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(MailAccount)
    if admin.role == "domain_admin" and admin.domain_id:
        stmt = stmt.where(MailAccount.domain_id == admin.domain_id)
    elif domain_id:
        stmt = stmt.where(MailAccount.domain_id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if q:
        keyword = q.strip().lower()
        stmt = stmt.where(or_(func.lower(MailAccount.email).contains(keyword), func.lower(func.coalesce(MailAccount.display_name, "")).contains(keyword)))
    if sort == "-created_at":
        stmt = stmt.order_by(MailAccount.created_at.desc())
    elif sort == "created_at":
        stmt = stmt.order_by(MailAccount.created_at.asc())
    else:
        stmt = stmt.order_by(MailAccount.email.asc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[account_to_admin_dict(item) for item in items]))


@router.post("/users", response_model=ApiResponse)
def create_admin_user_account(
    request: Request,
    payload: AdminUserCreateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    domain_uuid = _parse_uuid(payload.domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效") if payload.domain_id else None
    ensure_domain_scope(admin, domain_uuid)
    if db.scalar(select(MailAccount).where(func.lower(MailAccount.email) == payload.email.lower())):
        raise AppError("ADMIN_USER_EXISTS", "邮箱账号已存在", http_status=status.HTTP_400_BAD_REQUEST)
    if domain_uuid is not None and db.get(MailDomain, domain_uuid) is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    account = MailAccount(
        email=payload.email.lower(),
        display_name=payload.display_name,
        domain_id=domain_uuid,
        quota_mb=payload.quota_mb,
        status=payload.status,
        is_admin=payload.is_admin,
        imap_host=get_settings().mail_imap_host,
        imap_port=get_settings().mail_imap_port,
        imap_ssl=get_settings().mail_imap_ssl,
        smtp_host=get_settings().mail_smtp_host,
        smtp_port=get_settings().mail_smtp_port,
        smtp_ssl=get_settings().mail_smtp_ssl,
    )
    db.add(account)
    db.flush()
    record_admin_audit(request, admin, "admin.users.create", success=True, target_type="mail_account", target_id=str(account.id), metadata={"email": account.email})
    db.commit()
    db.refresh(account)
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.get("/users/{user_id}", response_model=ApiResponse)
def get_admin_user_account(
    request: Request,
    user_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.patch("/users/{user_id}", response_model=ApiResponse)
def update_admin_user_account(
    request: Request,
    user_id: str,
    payload: AdminUserUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    if payload.domain_id is not None:
        next_domain_id = _parse_uuid(payload.domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效")
        ensure_domain_scope(admin, next_domain_id)
        if db.get(MailDomain, next_domain_id) is None:
            raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
        account.domain_id = next_domain_id
    if payload.display_name is not None:
        account.display_name = payload.display_name
    if payload.quota_mb is not None:
        account.quota_mb = payload.quota_mb
    if payload.status is not None:
        account.status = payload.status
    if payload.is_admin is not None:
        account.is_admin = payload.is_admin
    record_admin_audit(request, admin, "admin.users.update", success=True, target_type="mail_account", target_id=str(account.id))
    db.commit()
    db.refresh(account)
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.delete("/users/{user_id}", response_model=ApiResponse)
def delete_admin_user_account(
    request: Request,
    user_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    db.delete(account)
    record_admin_audit(request, admin, "admin.users.delete", success=True, target_type="mail_account", target_id=user_id)
    db.commit()
    return success_response(request, {"deleted": True})


@router.post("/users/{user_id}/reset-password", response_model=ApiResponse)
def reset_admin_user_password(
    request: Request,
    user_id: str,
    payload: AdminUserResetPasswordRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    record_admin_audit(request, admin, "admin.users.reset_password", success=True, target_type="mail_account", target_id=user_id, metadata={"password_changed": True})
    db.commit()
    return success_response(request, {"password_reset": True, "password": payload.password})


@router.post("/users/bulk-action", response_model=ApiResponse)
def admin_users_bulk_action(
    request: Request,
    payload: AdminUsersBulkActionRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    uuids = [_parse_uuid(item, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效") for item in payload.ids]
    accounts = db.scalars(select(MailAccount).where(MailAccount.id.in_(uuids))).all()
    changed = 0
    for account in accounts:
        ensure_domain_scope(admin, account.domain_id)
        if payload.action == "activate":
            account.status = "active"
            changed += 1
        elif payload.action == "disable":
            account.status = "disabled"
            changed += 1
        elif payload.action == "delete":
            db.delete(account)
            changed += 1
    record_admin_audit(request, admin, "admin.users.bulk_action", success=True, target_type="mail_account", metadata={"action": payload.action, "count": changed})
    db.commit()
    return success_response(request, {"updated": changed})


@router.get("/aliases", response_model=ApiResponse)
def list_aliases(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = None,
    domain_id: str | None = None,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(MailAlias)
    if admin.role == "domain_admin" and admin.domain_id:
        stmt = stmt.where(MailAlias.domain_id == admin.domain_id)
    elif domain_id:
        stmt = stmt.where(MailAlias.domain_id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if q:
        keyword = q.strip().lower()
        stmt = stmt.where(func.lower(MailAlias.source_address).contains(keyword))
    stmt = stmt.order_by(MailAlias.created_at.desc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[alias_to_dict(item) for item in items]))


@router.post("/aliases", response_model=ApiResponse)
def create_alias(
    request: Request,
    payload: AliasCreateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    domain_uuid = _parse_uuid(payload.domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效")
    ensure_domain_scope(admin, domain_uuid)
    domain = db.get(MailDomain, domain_uuid)
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    targets = _ensure_target_addresses([str(item) for item in payload.target_addresses])
    _ensure_alias_not_conflict(db, source_address=str(payload.source_address))
    _ensure_alias_no_direct_loop(str(payload.source_address), targets)
    alias = MailAlias(
        domain_id=domain_uuid,
        source_address=str(payload.source_address).lower(),
        target_addresses=targets,
        is_active=True,
    )
    db.add(alias)
    db.flush()
    record_admin_audit(request, admin, "admin.aliases.create", success=True, target_type="mail_alias", target_id=str(alias.id))
    db.commit()
    db.refresh(alias)
    return success_response(request, {"alias": alias_to_dict(alias)})


@router.get("/aliases/{alias_id}", response_model=ApiResponse)
def get_alias(
    request: Request,
    alias_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    alias = db.get(MailAlias, _parse_uuid(alias_id, code="ADMIN_ALIAS_INVALID_ID", message="别名 ID 无效"))
    if alias is None:
        raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, alias.domain_id)
    return success_response(request, {"alias": alias_to_dict(alias)})


@router.patch("/aliases/{alias_id}", response_model=ApiResponse)
def update_alias(
    request: Request,
    alias_id: str,
    payload: AliasUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    alias = db.get(MailAlias, _parse_uuid(alias_id, code="ADMIN_ALIAS_INVALID_ID", message="别名 ID 无效"))
    if alias is None:
        raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, alias.domain_id)
    if payload.target_addresses is not None:
        targets = _ensure_target_addresses([str(item) for item in payload.target_addresses])
        _ensure_alias_no_direct_loop(alias.source_address, targets)
        alias.target_addresses = targets
    if payload.is_active is not None:
        alias.is_active = payload.is_active
    record_admin_audit(request, admin, "admin.aliases.update", success=True, target_type="mail_alias", target_id=str(alias.id))
    db.commit()
    db.refresh(alias)
    return success_response(request, {"alias": alias_to_dict(alias)})


@router.delete("/aliases/{alias_id}", response_model=ApiResponse)
def delete_alias(
    request: Request,
    alias_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    alias = db.get(MailAlias, _parse_uuid(alias_id, code="ADMIN_ALIAS_INVALID_ID", message="别名 ID 无效"))
    if alias is None:
        raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, alias.domain_id)
    db.delete(alias)
    record_admin_audit(request, admin, "admin.aliases.delete", success=True, target_type="mail_alias", target_id=alias_id)
    db.commit()
    return success_response(request, {"deleted": True})


@router.post("/aliases/{alias_id}/toggle", response_model=ApiResponse)
def toggle_alias(
    request: Request,
    alias_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    alias = db.get(MailAlias, _parse_uuid(alias_id, code="ADMIN_ALIAS_INVALID_ID", message="别名 ID 无效"))
    if alias is None:
        raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, alias.domain_id)
    alias.is_active = not alias.is_active
    record_admin_audit(request, admin, "admin.aliases.toggle", success=True, target_type="mail_alias", target_id=str(alias.id), metadata={"is_active": alias.is_active})
    db.commit()
    db.refresh(alias)
    return success_response(request, {"alias": alias_to_dict(alias)})


@router.get("/quotas", response_model=ApiResponse)
def list_quotas(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    domains = db.scalars(select(MailDomain).options(selectinload(MailDomain.accounts), selectinload(MailDomain.quota_policy))).all()
    if admin.role == "domain_admin" and admin.domain_id:
        domains = [item for item in domains if item.id == admin.domain_id]
    items: list[dict[str, Any]] = []
    for domain in domains:
        used_quota_mb = sum(account.quota_mb for account in domain.accounts or [])
        percentage = round((used_quota_mb / domain.quota_limit_mb) * 100, 2) if domain.quota_limit_mb else 0
        items.append(
            {
                **quota_policy_to_dict(domain.quota_policy, domain=domain),
                "quota_limit_mb": domain.quota_limit_mb,
                "used_quota_mb": used_quota_mb,
                "usage_percent": percentage,
                "status": "critical" if percentage >= 95 else "warning" if percentage >= 80 else "healthy",
            }
        )
    return success_response(request, {"items": items})


@router.patch("/quotas/policy", response_model=ApiResponse)
def update_quota_policy(
    request: Request,
    payload: QuotaPolicyUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    domain_uuid = _parse_uuid(payload.domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效") if payload.domain_id else None
    ensure_domain_scope(admin, domain_uuid)
    if domain_uuid is not None and db.get(MailDomain, domain_uuid) is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    policy = db.scalar(select(QuotaPolicy).where(QuotaPolicy.domain_id == domain_uuid))
    if policy is None:
        policy = QuotaPolicy(domain_id=domain_uuid)
        db.add(policy)
        db.flush()
    policy.default_quota_mb = payload.default_quota_mb
    policy.warn_80_enabled = payload.warn_80_enabled
    policy.warn_90_enabled = payload.warn_90_enabled
    policy.warn_95_enabled = payload.warn_95_enabled
    record_admin_audit(request, admin, "admin.quotas.update_policy", success=True, target_type="quota_policy", target_id=str(policy.id))
    db.commit()
    db.refresh(policy)
    return success_response(request, {"policy": quota_policy_to_dict(policy)})


@router.patch("/users/{user_id}/quota", response_model=ApiResponse)
def update_user_quota(
    request: Request,
    user_id: str,
    payload: UserQuotaUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    account.quota_mb = payload.quota_mb
    record_admin_audit(request, admin, "admin.quotas.update_user", success=True, target_type="mail_account", target_id=str(account.id), metadata={"quota_mb": payload.quota_mb})
    db.commit()
    db.refresh(account)
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.post("/quotas/bulk-update", response_model=ApiResponse)
def bulk_update_quotas(
    request: Request,
    payload: QuotaBulkUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    uuids = [_parse_uuid(item, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效") for item in payload.ids]
    accounts = db.scalars(select(MailAccount).where(MailAccount.id.in_(uuids))).all()
    changed = 0
    for account in accounts:
        ensure_domain_scope(admin, account.domain_id)
        account.quota_mb = payload.quota_mb
        changed += 1
    record_admin_audit(request, admin, "admin.quotas.bulk_update", success=True, target_type="mail_account", metadata={"count": changed, "quota_mb": payload.quota_mb})
    db.commit()
    return success_response(request, {"updated": changed})


@router.get("/audit-logs", response_model=ApiResponse)
def list_audit_logs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    event_type: str | None = None,
    actor_id: str | None = None,
    success_only: bool | None = None,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(AuditLog)
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if success_only is not None:
        stmt = stmt.where(AuditLog.success.is_(success_only))
    stmt = stmt.order_by(AuditLog.created_at.desc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[audit_log_to_dict(item) for item in items]))


@router.get("/system-health", response_model=ApiResponse)
@router.get("/system/health", response_model=ApiResponse, include_in_schema=False)
def admin_system_health(request: Request, admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db_session)) -> dict[str, Any]:
    _ = admin
    now = datetime.now(UTC).isoformat()
    items = [
        {
            "name": "database",
            "status": "ok" if db.execute(select(1)).scalar() == 1 else "down",
            "detail": "数据库连接正常",
        },
        {
            "name": "redis",
            "status": "ok",
            "detail": "Redis 已配置",
        },
        {
            "name": "application",
            "status": "ok",
            "detail": f"应用健康检查时间 {now}",
        },
    ]
    return success_response(request, {"items": items, "checked_at": now})


@router.get("/overview", response_model=ApiResponse)
@router.get("/dashboard/overview", response_model=ApiResponse, include_in_schema=False)
def dashboard_overview(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    metrics = count_dashboard_metrics(db)
    recent_logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)).all()
    return success_response(
        request,
        {
            "active_users": metrics["active_admin_total"],
            "mail_domains": metrics["domain_total"],
            "aliases": metrics["alias_total"],
            "queued_jobs": 0,
            "summary": metrics,
            "recent_audits": [audit_log_to_dict(item) for item in recent_logs],
            "scope": {"role": admin.role, "domain_id": str(admin.domain_id) if admin.domain_id else None},
        },
    )


@router.get("/dashboard/trends", response_model=ApiResponse)
def dashboard_trends(request: Request, admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db_session)) -> dict[str, Any]:
    _ = admin
    today = utcnow().date()
    points: list[dict[str, Any]] = []
    for offset in range(6, -1, -1):
        day = today.fromordinal(today.toordinal() - offset)
        next_day = day.fromordinal(day.toordinal() + 1)
        count = int(
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.created_at >= day, AuditLog.created_at < next_day)
            )
            or 0
        )
        points.append({"date": day.isoformat(), "audit_count": count})
    return success_response(request, {"points": points})
