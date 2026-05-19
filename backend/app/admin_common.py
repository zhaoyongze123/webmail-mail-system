"""后台管理模块的通用上下文、权限与 DTO 转换工具。"""

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
from app.models import AdminRefreshToken, AdminUser, AuditLog, MailAlias, MailDomain, MailAccount, MailMessage, QuotaPolicy
from app.observability import record_audit_event


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class AdminContext:
    """当前后台请求中解析出的管理员上下文。"""

    user_id: UUID
    username: str
    role: str
    domain_id: UUID | None


def utcnow() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(UTC)


def normalize_utc(value: datetime) -> datetime:
    """将任意 datetime 规范化为 UTC 时区。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_db_session():
    """FastAPI 依赖项：创建数据库会话并在请求结束后关闭。"""
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def paginate(*, page: int, page_size: int, total: int, items: list[Any]) -> dict[str, Any]:
    """构造通用分页响应结构。"""
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": ceil(total / page_size) if page_size else 0,
        "items": items,
    }


def normalize_pagination(page: int = DEFAULT_PAGE, page_size: int = DEFAULT_PAGE_SIZE) -> tuple[int, int]:
    """将分页参数约束到安全范围内。"""
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    return safe_page, safe_page_size


def ensure_domain_scope(admin: AdminContext, domain_id: UUID | None) -> None:
    """校验当前管理员是否有权访问指定域。"""
    if admin.role == "superadmin":
        return
    if admin.domain_id is None or domain_id != admin.domain_id:
        raise AppError(
            "ADMIN_FORBIDDEN",
            "无权访问该域资源",
            http_status=status.HTTP_403_FORBIDDEN,
        )


def ensure_superadmin(admin: AdminContext) -> None:
    """校验当前管理员是否为超级管理员。"""
    if admin.role != "superadmin":
        raise AppError(
            "ADMIN_FORBIDDEN",
            "仅超级管理员可执行该操作",
            http_status=status.HTTP_403_FORBIDDEN,
        )


def ensure_active_admin(user: AdminUser | None) -> AdminUser:
    """校验管理员账号存在且启用。"""
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
    """记录后台管理操作审计日志。"""
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
    """将域对象转换为后台接口输出字典。"""
    user_count = len(domain.accounts or [])
    alias_count = len(domain.aliases or [])
    used_quota_mb = sum(float(getattr(account, "_used_quota_mb", 0.0) or 0.0) for account in domain.accounts or [])
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


def quota_usage_status(usage_percent: float) -> str:
    """根据使用率返回统一的配额状态。"""
    if usage_percent >= 95:
        return "critical"
    if usage_percent >= 80:
        return "warning"
    return "healthy"


def load_account_usage_map(db: Session, account_ids: list[UUID]) -> dict[UUID, float]:
    """聚合账号已缓存邮件体积，换算为 MB 使用量。"""
    if not account_ids:
        return {}
    rows = db.execute(
        select(MailMessage.account_id, func.coalesce(func.sum(MailMessage.size_bytes), 0))
        .where(MailMessage.account_id.in_(account_ids))
        .group_by(MailMessage.account_id)
    ).all()
    usage_map: dict[UUID, float] = {}
    for account_id, size_bytes in rows:
        usage_map[account_id] = round(float(size_bytes or 0) / (1024 * 1024), 2)
    return usage_map


def account_to_admin_dict(account: MailAccount, *, used_quota_mb: float | None = None) -> dict[str, Any]:
    """将邮箱账号转换为后台接口输出字典。"""
    resolved_used_quota_mb = round(float(used_quota_mb if used_quota_mb is not None else getattr(account, "_used_quota_mb", 0.0) or 0.0), 2)
    usage_percent = round((resolved_used_quota_mb / account.quota_mb) * 100, 2) if account.quota_mb else 0.0
    return {
        "id": str(account.id),
        "name": account.display_name or account.email,
        "email": account.email,
        "display_name": account.display_name,
        "domain_id": str(account.domain_id) if account.domain_id else None,
        "domain_name": account.domain.name if getattr(account, "domain", None) else None,
        "quota_mb": account.quota_mb,
        "used_quota_mb": resolved_used_quota_mb,
        "usage_percent": usage_percent,
        "quota_status": quota_usage_status(usage_percent),
        "usage_source": getattr(account, "_usage_source", "cached"),
        "status": account.status,
        "is_admin": account.is_admin,
        "has_local_password": bool(account.password_hash),
        "description": "管理员账号" if account.is_admin else "普通邮箱账号",
        "last_login_at": account.last_login_at.isoformat() if account.last_login_at else None,
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
    }


def alias_to_dict(alias: MailAlias) -> dict[str, Any]:
    """将别名对象转换为后台接口输出字典。"""
    return {
        "id": str(alias.id),
        "name": alias.source_address,
        "domain_id": str(alias.domain_id),
        "domain_name": alias.domain.name if getattr(alias, "domain", None) else None,
        "source_address": alias.source_address,
        "target_addresses": list(alias.target_addresses or []),
        "is_active": alias.is_active,
        "status": "active" if alias.is_active else "disabled",
        "description": " -> ".join(alias.target_addresses or []),
        "created_at": alias.created_at.isoformat(),
        "updated_at": alias.updated_at.isoformat(),
    }


def quota_policy_to_dict(policy: QuotaPolicy | None, *, domain: MailDomain | None = None) -> dict[str, Any]:
    """将配额策略转换为后台接口输出字典。"""
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
    """将审计日志对象转换为后台接口输出字典。"""
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
    """清理已过期的后台刷新令牌。"""
    stmt = select(AdminRefreshToken).where(AdminRefreshToken.revoked_at.is_(None))
    if user_id is not None:
        stmt = stmt.where(AdminRefreshToken.admin_user_id == user_id)
    for token in db.scalars(stmt).all():
        if normalize_utc(token.expires_at) <= utcnow():
            token.revoked_at = utcnow()


def count_dashboard_metrics(db: Session) -> dict[str, int]:
    """统计后台仪表盘所需的核心指标。"""
    return {
        "domain_total": int(db.scalar(select(func.count()).select_from(MailDomain)) or 0),
        "user_total": int(db.scalar(select(func.count()).select_from(MailAccount)) or 0),
        "alias_total": int(db.scalar(select(func.count()).select_from(MailAlias)) or 0),
        "active_admin_total": int(
            db.scalar(select(func.count()).select_from(AdminUser).where(AdminUser.is_active.is_(True))) or 0
        ),
    }
