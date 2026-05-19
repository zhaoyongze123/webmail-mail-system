from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from typing import Any
from uuid import UUID

from fastapi import Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session_factory
from app.errors import AppError
from app.models import AdminRefreshToken, AdminUser, AuditLog, MailAlias, MailDomain, MailAccount, QuotaPolicy
from app.observability import record_audit_event


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class AdminContext:
    user_id: UUID
    username: str
    role: str
    domain_id: UUID | None


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_db_session() -> Session:
    session_factory = get_session_factory()
    return session_factory()


def paginate(*, page: int, page_size: int, total: int, items: list[Any]) -> dict[str, Any]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": ceil(total / page_size) if page_size else 0,
        "items": items,
    }


def normalize_pagination(page: int = DEFAULT_PAGE, page_size: int = DEFAULT_PAGE_SIZE) -> tuple[int, int]:
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    return safe_page, safe_page_size


def ensure_domain_scope(admin: AdminContext, domain_id: UUID | None) -> None:
    if admin.role == "superadmin":
        return
    if admin.domain_id is None or domain_id != admin.domain_id:
        raise AppError(
            "ADMIN_FORBIDDEN",
            "无权访问该域资源",
            http_status=status.HTTP_403_FORBIDDEN,
        )


def ensure_superadmin(admin: AdminContext) -> None:
    if admin.role != "superadmin":
        raise AppError(
            "ADMIN_FORBIDDEN",
            "仅超级管理员可执行该操作",
            http_status=status.HTTP_403_FORBIDDEN,
        )


def ensure_active_admin(user: AdminUser | None) -> AdminUser:
    if user is None or not user.is_active:
        raise AppError(
            "ADMIN_AUTH_INVALID",
            "管理员不存在或已停用",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    return user


def record_admin_audit(
    request: Request,
    admin: AdminContext,
    event_type: str,
    *,
    success: bool,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    payload = dict(metadata or {})
    payload.setdefault("admin_username", admin.username)
    record_audit_event(
        request,
        event_type,
        success=success,
        metadata=payload,
        actor_type="admin_user",
        actor_id=str(admin.user_id),
        target_type=target_type,
        target_id=target_id,
    )


def domain_to_dict(domain: MailDomain) -> dict[str, Any]:
    user_count = len(domain.accounts or [])
    alias_count = len(domain.aliases or [])
    used_quota_mb = sum(account.quota_mb for account in domain.accounts or [])
    return {
        "id": str(domain.id),
        "name": domain.name,
        "quota_limit_mb": domain.quota_limit_mb,
        "status": domain.status,
        "description": f"用户 {user_count} / 别名 {alias_count} / 已用 {used_quota_mb} MB",
        "user_count": user_count,
        "alias_count": alias_count,
        "used_quota_mb": used_quota_mb,
        "created_at": domain.created_at.isoformat(),
        "updated_at": domain.updated_at.isoformat(),
    }


def account_to_admin_dict(account: MailAccount) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "name": account.display_name or account.email,
        "email": account.email,
        "display_name": account.display_name,
        "domain_id": str(account.domain_id) if account.domain_id else None,
        "quota_mb": account.quota_mb,
        "status": account.status,
        "is_admin": account.is_admin,
        "description": "管理员账号" if account.is_admin else "普通邮箱账号",
        "last_login_at": account.last_login_at.isoformat() if account.last_login_at else None,
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
    }


def alias_to_dict(alias: MailAlias) -> dict[str, Any]:
    return {
        "id": str(alias.id),
        "name": alias.source_address,
        "domain_id": str(alias.domain_id),
        "source_address": alias.source_address,
        "target_addresses": list(alias.target_addresses or []),
        "is_active": alias.is_active,
        "status": "active" if alias.is_active else "disabled",
        "description": " -> ".join(alias.target_addresses or []),
        "created_at": alias.created_at.isoformat(),
        "updated_at": alias.updated_at.isoformat(),
    }


def quota_policy_to_dict(policy: QuotaPolicy | None, *, domain: MailDomain | None = None) -> dict[str, Any]:
    return {
        "id": str(policy.id) if policy else None,
        "name": domain.name if domain else "全局默认",
        "domain_id": str(policy.domain_id) if policy and policy.domain_id else None,
        "domain_name": domain.name if domain else None,
        "default_quota_mb": policy.default_quota_mb if policy else 500,
        "warn_80_enabled": policy.warn_80_enabled if policy else True,
        "warn_90_enabled": policy.warn_90_enabled if policy else True,
        "warn_95_enabled": policy.warn_95_enabled if policy else True,
        "updated_at": policy.updated_at.isoformat() if policy else None,
    }


def audit_log_to_dict(log: AuditLog) -> dict[str, Any]:
    return {
        "id": str(log.id),
        "actor": log.actor_id or log.account_id or "system",
        "action": log.event_type,
        "target": log.target_id or log.target_type or "-",
        "event_type": log.event_type,
        "account_id": str(log.account_id) if log.account_id else None,
        "actor_type": log.actor_type,
        "actor_id": log.actor_id,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "request_id": log.request_id,
        "ip": log.ip,
        "success": log.success,
        "metadata": log.metadata_ or {},
        "created_at": log.created_at.isoformat(),
    }


def cleanup_refresh_tokens(db: Session, *, user_id: UUID | None = None) -> None:
    stmt = select(AdminRefreshToken).where(AdminRefreshToken.revoked_at.is_(None))
    if user_id is not None:
        stmt = stmt.where(AdminRefreshToken.admin_user_id == user_id)
    for token in db.scalars(stmt).all():
        if normalize_utc(token.expires_at) <= utcnow():
            token.revoked_at = utcnow()


def count_dashboard_metrics(db: Session) -> dict[str, int]:
    return {
        "domain_total": int(db.scalar(select(func.count()).select_from(MailDomain)) or 0),
        "user_total": int(db.scalar(select(func.count()).select_from(MailAccount)) or 0),
        "alias_total": int(db.scalar(select(func.count()).select_from(MailAlias)) or 0),
        "active_admin_total": int(
            db.scalar(select(func.count()).select_from(AdminUser).where(AdminUser.is_active.is_(True))) or 0
        ),
    }
