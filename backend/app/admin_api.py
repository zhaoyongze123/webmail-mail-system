"""后台管理 API 路由：认证、域、用户、别名、配额、审计与运维能力。"""

from __future__ import annotations

from datetime import UTC, datetime
import secrets
import csv
from io import StringIO
from pathlib import Path
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
    cleanup_admin_logs,
    count_dashboard_metrics,
    domain_to_dict,
    ensure_domain_scope,
    ensure_superadmin,
    get_db_session,
    load_account_usage_map,
    paginate,
    quota_policy_to_dict,
    quota_usage_status,
    record_admin_audit,
    normalize_pagination,
    utcnow,
)
from app.admin_system import (
    backup_config_file,
    check_admin_ip_access,
    clear_mail_queue,
    control_mail_service,
    delete_mail_queue_items,
    delete_mail_queue_item,
    export_log_items,
    flush_mail_queue,
    get_domain_dkim_info,
    get_mailbox_quota_usage,
    get_mail_queue_message,
    get_mail_queue_snapshot,
    get_mail_system_configs,
    get_admin_ip_policy,
    get_cpu_usage_snapshot,
    get_memory_usage_snapshot,
    get_online_dovecot_users,
    get_rspamd_thresholds,
    get_tls_certificates,
    list_disk_usage,
    list_mail_service_logs,
    list_mail_queue,
    list_service_health,
    recalc_mailbox_quota_usage,
    rebuild_postfix_maps,
    rebuild_system_aliases,
    requeue_mail_queue_item,
    reload_dovecot_service,
    reload_postfix_service,
    renew_tls_certificates,
    restore_config_backup,
    rotate_domain_dkim_key,
    run_domain_dns_check,
    search_mail_service_logs,
    update_rspamd_thresholds,
)
from app.auth import hash_mailbox_password
from app.config import get_settings
from app.errors import AppError
from app.mail_directory import (
    create_directory_account,
    create_directory_domain,
    delete_directory_account,
    delete_directory_domain,
    delete_shadow_account,
    delete_shadow_domain,
    ensure_shadow_account,
    ensure_shadow_domain,
    get_directory_account,
    list_directory_accounts,
    list_directory_domains,
    rename_directory_domain,
    sync_directory_shadow,
    update_directory_account_password,
    use_sqlite_mail_directory,
)
from app.models import AdminActionHistory, AdminSystemSetting, AdminUser, AuditLog, MailAccount, MailAlias, MailDomain, QuotaPolicy
from app.redis_client import get_redis_client
from app.responses import success_response
from app.schemas import ApiResponse


router = APIRouter(prefix="/api/admin", tags=["admin"], responses={401: {"description": "未授权"}})


class BulkStatusRequest(BaseModel):
    """批量修改状态请求体。"""

    ids: list[str] = Field(default_factory=list, min_length=1)
    status: str = Field(pattern="^(active|disabled)$")


class DomainCreateRequest(BaseModel):
    """创建域的请求体。"""

    name: str = Field(min_length=3, max_length=255)
    quota_limit_mb: int = Field(default=10240, ge=1, le=1024 * 1024)
    status: str = Field(default="active", pattern="^(active|disabled)$")


class DomainUpdateRequest(BaseModel):
    """更新域信息的请求体。"""

    name: str | None = Field(default=None, min_length=3, max_length=255)
    quota_limit_mb: int | None = Field(default=None, ge=1, le=1024 * 1024)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class AdminUserCreateRequest(BaseModel):
    """创建后台可管理邮箱账号的请求体。"""

    email: EmailStr
    display_name: str | None = Field(default=None, max_length=255)
    domain_id: str | None = None
    password: str = Field(min_length=8, max_length=256)
    quota_mb: int = Field(default=500, ge=1, le=1024 * 1024)
    status: str = Field(default="active", pattern="^(active|disabled)$")
    is_admin: bool = False


class AdminUserUpdateRequest(BaseModel):
    """更新后台可管理邮箱账号的请求体。"""

    display_name: str | None = Field(default=None, max_length=255)
    domain_id: str | None = None
    quota_mb: int | None = Field(default=None, ge=1, le=1024 * 1024)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    is_admin: bool | None = None


class AdminUserResetPasswordRequest(BaseModel):
    """重置邮箱账号密码的请求体。"""

    password: str | None = Field(default=None, min_length=8, max_length=256)
    generate_random: bool = False


class AdminUsersBulkActionRequest(BaseModel):
    """邮箱账号批量操作请求体。"""

    ids: list[str] = Field(default_factory=list, min_length=1)
    action: str = Field(pattern="^(activate|disable|delete)$")


class AliasCreateRequest(BaseModel):
    """创建邮件别名的请求体。"""

    domain_id: str
    source_address: EmailStr
    target_addresses: list[EmailStr] = Field(default_factory=list, min_length=1)
    is_catch_all: bool = False
    catch_all_target: EmailStr | None = None


class AliasUpdateRequest(BaseModel):
    """更新邮件别名的请求体。"""

    target_addresses: list[EmailStr] | None = None
    is_active: bool | None = None


class QuotaPolicyUpdateRequest(BaseModel):
    """更新域配额策略的请求体。"""

    domain_id: str | None = None
    default_quota_mb: int = Field(ge=1, le=1024 * 1024)
    warn_80_enabled: bool = True
    warn_90_enabled: bool = True
    warn_95_enabled: bool = True


class UserQuotaUpdateRequest(BaseModel):
    """更新单个邮箱账号配额的请求体。"""

    quota_mb: int = Field(ge=1, le=1024 * 1024)


class QuotaBulkUpdateRequest(BaseModel):
    """批量更新邮箱账号配额的请求体。"""

    ids: list[str] = Field(default_factory=list, min_length=1)
    quota_mb: int = Field(ge=1, le=1024 * 1024)


class QueueDeleteRequest(BaseModel):
    """删除指定队列邮件的请求体。"""

    queue_id: str = Field(min_length=1, max_length=255)


class QueueBulkDeleteRequest(BaseModel):
    """批量删除或清空队列的请求体。"""

    queue_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)


class QueueActionRequest(BaseModel):
    """队列单项操作请求体。"""

    queue_id: str = Field(min_length=1, max_length=255)


class LogsExportRequest(BaseModel):
    """日志导出请求体。"""

    log_key: str | None = None
    q: str | None = None
    status: str | None = None
    sender: str | None = None
    recipient: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    format: str = Field(default="csv", pattern="^(csv|json)$")


class UserCsvImportRequest(BaseModel):
    """用户 CSV 导入请求。"""

    csv_content: str = Field(min_length=1)
    domain_id: str | None = None


class AliasCatchAllRequest(BaseModel):
    """一键生成 catch-all 别名请求。"""

    domain_id: str
    target_address: EmailStr


class ConfigRestoreRequest(BaseModel):
    """恢复配置文件请求体。"""

    backup_path: str = Field(min_length=1, max_length=1024)
    target_path: str = Field(min_length=1, max_length=1024)


class ServiceActionRequest(BaseModel):
    """服务控制请求体。"""

    service: str = Field(min_length=1, max_length=50)
    action: str = Field(min_length=1, max_length=20)


class SystemConfigUpdateRequest(BaseModel):
    """后台系统配置更新请求体。"""

    theme: str = Field(default="system", pattern="^(system|light|dark)$")
    language: str = Field(default="zh-CN", pattern="^(zh-CN|en-US)$")
    queue_auto_refresh_seconds: int = Field(default=15, ge=5, le=3600)
    queue_max_items: int = Field(default=100, ge=10, le=1000)
    audit_default_days: int = Field(default=30, ge=1, le=3650)
    log_retention_days: int = Field(default=14, ge=1, le=3650)


class RspamdThresholdUpdateRequest(BaseModel):
    """更新 Rspamd 垃圾分阈值的请求体。"""

    reject: float = Field(ge=0, le=100)
    add_header: float = Field(ge=0, le=100)
    greylist: float = Field(ge=0, le=100)


class DkimRotateRequest(BaseModel):
    """轮换 DKIM 私钥的请求体。"""

    selector: str | None = Field(default=None, min_length=1, max_length=64)


class TlsRenewRequest(BaseModel):
    """TLS 续签请求体。"""

    confirm: bool = True


class ActionHistoryQuery(BaseModel):
    """后台历史记录查询参数占位类型。"""


class AuditLogExportRequest(BaseModel):
    """审计日志导出请求体。"""

    q: str | None = None
    action: str | None = None
    actor_id: str | None = None
    target: str | None = None
    success_only: bool | None = None
    date_from: str | None = None
    date_to: str | None = None
    format: str = Field(default="csv", pattern="^(csv|json)$")


def _parse_uuid(value: str, *, code: str, message: str) -> UUID:
    """将字符串解析为 UUID，失败时抛出标准应用异常。"""
    try:
        return UUID(value)
    except ValueError as exc:
        raise AppError(code, message, http_status=status.HTTP_400_BAD_REQUEST) from exc


def _normalize_domain_name(name: str) -> str:
    """规范化域名并执行基础格式校验。"""
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
    """确保别名目标地址非空并统一小写。"""
    normalized = [item.strip().lower() for item in targets if item.strip()]
    if not normalized:
        raise AppError(
            "ADMIN_ALIAS_TARGET_REQUIRED",
            "别名必须至少包含一个目标地址",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    return normalized


def _ensure_alias_not_conflict(db: Session, *, source_address: str, alias_id: UUID | None = None) -> None:
    """确保别名源地址不与现有账号或别名冲突。"""
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
    """避免别名形成直接自引用循环。"""
    source = source_address.strip().lower()
    normalized_targets = [item.strip().lower() for item in target_addresses]
    if source in normalized_targets:
        raise AppError(
            "ADMIN_ALIAS_LOOP",
            "别名目标地址不能包含自身",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    direct_pairs = {(source, target) for target in normalized_targets}
    if any(source == target for _, target in direct_pairs):
        raise AppError(
            "ADMIN_ALIAS_LOOP",
            "别名目标地址不能形成直接循环",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


def _alias_graph(db: Session, *, domain_id: UUID | None = None) -> dict[str, list[str]]:
    """读取当前别名图用于递归循环检测。"""
    stmt = select(MailAlias)
    if domain_id is not None:
        stmt = stmt.where(MailAlias.domain_id == domain_id)
    aliases = db.scalars(stmt).all()
    graph: dict[str, list[str]] = {}
    for alias in aliases:
        graph[alias.source_address.strip().lower()] = [item.strip().lower() for item in alias.target_addresses or []]
    return graph


def _detect_alias_cycle(graph: dict[str, list[str]], source_address: str, target_addresses: list[str]) -> None:
    """检测别名图里是否会形成多跳递归循环。"""
    source = source_address.strip().lower()
    pending = [item.strip().lower() for item in target_addresses]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == source:
            raise AppError("ADMIN_ALIAS_LOOP", "别名目标地址形成了递归循环", http_status=status.HTTP_400_BAD_REQUEST)
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.get(current, []))


def _parse_user_csv(csv_content: str) -> list[dict[str, str]]:
    """解析用户导入 CSV。"""
    reader = csv.DictReader(StringIO(csv_content))
    rows: list[dict[str, str]] = []
    for row in reader:
        email = str(row.get("email") or "").strip()
        password = str(row.get("password") or "").strip()
        if not email or not password:
            raise AppError("ADMIN_USER_IMPORT_INVALID", "CSV 中 email 和 password 不能为空", http_status=status.HTTP_400_BAD_REQUEST)
        rows.append(
            {
                "email": email,
                "display_name": str(row.get("display_name") or "").strip(),
                "quota_mb": str(row.get("quota_mb") or "500").strip(),
                "status": str(row.get("status") or "active").strip().lower(),
                "is_admin": str(row.get("is_admin") or "false").strip().lower(),
                "password": password,
            }
        )
    if not rows:
        raise AppError("ADMIN_USER_IMPORT_EMPTY", "CSV 中没有可导入的用户", http_status=status.HTTP_400_BAD_REQUEST)
    return rows


def _parse_bool_text(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _email_domain(email: str) -> str:
    """提取邮箱地址中的域名部分。"""
    return email.strip().lower().split("@", 1)[1]


def _resolve_target_domain(
    db: Session,
    *,
    admin: AdminContext,
    payload_domain_id: str | None,
    email: str | None = None,
) -> MailDomain | None:
    """根据管理员权限、显式域 ID 或邮箱后缀推导目标域。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    if admin.role == "domain_admin":
        if admin.domain_id is None:
            raise AppError("ADMIN_FORBIDDEN", "当前管理员未绑定域", http_status=status.HTTP_403_FORBIDDEN)
        domain = db.get(MailDomain, admin.domain_id)
        if domain is None:
            raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
        if payload_domain_id and str(domain.id) != payload_domain_id:
            raise AppError("ADMIN_FORBIDDEN", "无权操作其他域资源", http_status=status.HTTP_403_FORBIDDEN)
        return domain
    if payload_domain_id:
        domain = db.get(MailDomain, _parse_uuid(payload_domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
        if domain is None:
            raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
        return domain
    if email:
        return db.scalar(select(MailDomain).where(MailDomain.name == _email_domain(email)))
    return None


def _load_rspamd_domains(admin: AdminContext, db: Session) -> list[MailDomain]:
    """按当前管理员权限加载可见域名列表。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    stmt = select(MailDomain).order_by(MailDomain.name.asc())
    domains = db.scalars(stmt).all()
    if admin.role == "domain_admin" and admin.domain_id:
        domains = [domain for domain in domains if domain.id == admin.domain_id]
    return domains


def _extract_dns_check(checks: list[dict[str, object]], key: str) -> dict[str, object]:
    """从 DNS 检测结果中提取指定检查项。"""
    for item in checks:
        if item.get("key") == key:
            return item
    return {"status": "unavailable", "detail": f"缺少 {key} 检测结果", "records": []}


def _build_rspamd_domain_payload(domain: MailDomain) -> dict[str, Any]:
    """构造 Rspamd 域级状态聚合结果。"""
    dns_result = run_domain_dns_check(domain.name)
    spf = _extract_dns_check(dns_result["checks"], "spf")
    dmarc = _extract_dns_check(dns_result["checks"], "dmarc")
    dkim_dns = _extract_dns_check(dns_result["checks"], "dkim")
    dkim_local = get_domain_dkim_info(domain.name)
    return {
        "id": str(domain.id),
        "name": domain.name,
        "spf_status": spf.get("status"),
        "spf_detail": spf.get("detail"),
        "spf_records": spf.get("records", []),
        "dmarc_status": dmarc.get("status"),
        "dmarc_detail": dmarc.get("detail"),
        "dmarc_records": dmarc.get("records", []),
        "dkim_dns_status": dkim_dns.get("status"),
        "dkim_dns_detail": dkim_dns.get("detail"),
        "dkim_dns_records": dkim_dns.get("records", []),
        "dkim_selector": dkim_local.get("selector"),
        "dkim_local_status": dkim_local.get("status"),
        "dkim_local_detail": dkim_local.get("detail"),
        "dkim_key_path": dkim_local.get("path"),
        "dkim_key_exists": dkim_local.get("exists", False),
        "dkim_public_key": dkim_local.get("public_key"),
    }


def _ensure_email_matches_domain(email: str, domain: MailDomain | None) -> None:
    """校验邮箱地址是否属于目标域。"""
    if domain is None:
        return
    if _email_domain(email) != domain.name:
        raise AppError(
            "ADMIN_USER_DOMAIN_MISMATCH",
            "邮箱地址与所选域不一致",
            http_status=status.HTTP_400_BAD_REQUEST,
        )


def _attach_account_usage(db: Session, accounts: list[MailAccount]) -> dict[UUID, float]:
    """为账号对象补充缓存用量，便于多个接口复用展示字段。"""
    usage_map = load_account_usage_map(db, [account.id for account in accounts])
    for account in accounts:
        setattr(account, "_used_quota_mb", usage_map.get(account.id, 0.0))
        setattr(account, "_usage_source", "cached")
    return usage_map


def _attach_live_quota_usage(db: Session, accounts: list[MailAccount]) -> dict[UUID, float]:
    """优先使用 doveadm 读取实时配额，失败时回退数据库缓存聚合。"""
    fallback_usage_map = load_account_usage_map(db, [account.id for account in accounts])
    usage_map: dict[UUID, float] = {}
    for account in accounts:
        quota_result = get_mailbox_quota_usage(account.email)
        if quota_result["status"] == "ok" and quota_result["used_quota_mb"] is not None:
            usage_value = float(quota_result["used_quota_mb"])
            usage_map[account.id] = usage_value
            setattr(account, "_used_quota_mb", usage_value)
            setattr(account, "_usage_source", quota_result["usage_source"])
            continue
        usage_value = fallback_usage_map.get(account.id, 0.0)
        usage_map[account.id] = usage_value
        setattr(account, "_used_quota_mb", usage_value)
        setattr(account, "_usage_source", f"fallback:{quota_result['usage_source']}")
    return usage_map


def _get_or_create_system_setting(db: Session) -> AdminSystemSetting:
    """获取后台系统配置；首次访问时按默认值初始化。"""
    setting = db.scalar(select(AdminSystemSetting).limit(1))
    if setting is None:
        setting = AdminSystemSetting()
        db.add(setting)
        db.flush()
    return setting


def _system_setting_to_dict(setting: AdminSystemSetting) -> dict[str, Any]:
    """将后台系统配置实体转换为接口输出。"""
    return {
        "theme": setting.theme,
        "language": setting.language,
        "queue_auto_refresh_seconds": setting.queue_auto_refresh_seconds,
        "queue_max_items": setting.queue_max_items,
        "audit_default_days": setting.audit_default_days,
        "log_retention_days": setting.log_retention_days,
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
        "detail": "当前后台系统配置已加载",
    }


def _record_admin_action_history(
    db: Session,
    *,
    admin: AdminContext,
    action_type: str,
    status_value: str,
    detail: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | list[Any] | None = None,
) -> None:
    """记录后台危险操作执行历史。"""
    db.add(
        AdminActionHistory(
            admin_user_id=admin.user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            status=status_value,
            detail=detail,
            payload=payload,
        )
    )


def _generate_random_password(length: int = 14) -> str:
    """生成用于后台密码重置的随机密码。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _parse_date_range(
    date_from: str | None,
    date_to: str | None,
) -> tuple[datetime | None, datetime | None]:
    """将日期字符串解析为 UTC 时间范围。"""
    start: datetime | None = None
    end: datetime | None = None
    if date_from:
        start = datetime.fromisoformat(f"{date_from}T00:00:00+00:00")
    if date_to:
        end = datetime.fromisoformat(f"{date_to}T23:59:59.999999+00:00")
    return start, end


@router.post("/auth/login", response_model=ApiResponse)
def admin_login(request: Request, payload: AdminLoginRequest, db: Session = Depends(get_db_session)) -> dict[str, Any]:
    """后台管理员登录接口。"""
    user, bundle = authenticate_admin(request, payload, db)
    db.commit()
    return success_response(request, build_auth_payload(user, bundle))


@router.post("/auth/refresh", response_model=ApiResponse)
def admin_refresh(request: Request, payload: AdminRefreshRequest, db: Session = Depends(get_db_session)) -> dict[str, Any]:
    """后台管理员刷新令牌接口。"""
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
    """后台管理员退出登录接口。"""
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
    """返回当前后台管理员的基本信息。"""
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
    """修改当前后台管理员密码。"""
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
    """初始化当前后台管理员的 TOTP 配置。"""
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
    """启用当前后台管理员的 TOTP。"""
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
    """禁用当前后台管理员的 TOTP。"""
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
    status_filter: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="name"),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """分页列出可管理的域列表。"""
    ensure_superadmin(admin)
    page, page_size = normalize_pagination(page, page_size)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
        items: list[dict[str, Any]] = []
        directory_domains = list_directory_domains()
        domain_names = [item.name for item in directory_domains]
        shadow_domains = db.scalars(
            select(MailDomain).where(MailDomain.name.in_(domain_names)).options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
        ).all() if domain_names else []
        shadow_map = {item.name: item for item in shadow_domains}
        for item in directory_domains:
            if q and q.strip().lower() not in item.name.lower():
                continue
            shadow_domain = shadow_map.get(item.name) or ensure_shadow_domain(db, item.name)
            if status_filter and shadow_domain.status != status_filter:
                continue
            _attach_account_usage(db, list(shadow_domain.accounts or []))
            domain_payload = domain_to_dict(shadow_domain)
            domain_payload["user_count"] = item.account_count
            domain_payload["description"] = f"用户 {item.account_count} / 别名 {domain_payload['alias_count']} / 已用 {domain_payload['used_quota_mb']} MB"
            items.append(domain_payload)
        if sort == "-created_at":
            items.sort(key=lambda value: value.get("created_at") or "", reverse=True)
        elif sort == "created_at":
            items.sort(key=lambda value: value.get("created_at") or "")
        else:
            items.sort(key=lambda value: str(value.get("name") or ""))
        total = len(items)
        paged_items = items[(page - 1) * page_size : page * page_size]
        db.commit()
        return success_response(request, paginate(page=page, page_size=page_size, total=total, items=paged_items))
    stmt = select(MailDomain).options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
    if q:
        stmt = stmt.where(func.lower(MailDomain.name).contains(q.strip().lower()))
    if status_filter:
        stmt = stmt.where(MailDomain.status == status_filter)
    if sort == "-created_at":
        stmt = stmt.order_by(MailDomain.created_at.desc())
    elif sort == "created_at":
        stmt = stmt.order_by(MailDomain.created_at.asc())
    else:
        stmt = stmt.order_by(MailDomain.name.asc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    domains = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    _attach_account_usage(db, [account for domain in domains for account in domain.accounts or []])
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[domain_to_dict(item) for item in domains]))


@router.post("/domains", response_model=ApiResponse)
def create_domain(
    request: Request,
    payload: DomainCreateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """创建新域。"""
    ensure_superadmin(admin)
    normalized = _normalize_domain_name(payload.name)
    if use_sqlite_mail_directory():
        directory_domain = create_directory_domain(normalized)
        domain = ensure_shadow_domain(db, directory_domain.name)
        domain.quota_limit_mb = payload.quota_limit_mb
        domain.status = payload.status
        record_admin_audit(request, admin, "admin.domains.create", success=True, target_type="domain", target_id=str(domain.id), metadata={"name": normalized})
        db.commit()
        db.refresh(domain)
        return success_response(request, {"domain": domain_to_dict(domain)})
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
    """获取单个域详情。"""
    ensure_superadmin(admin)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    domain = db.scalar(
        select(MailDomain)
        .where(MailDomain.id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
        .options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
    )
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    _attach_account_usage(db, list(domain.accounts or []))
    return success_response(request, {"domain": domain_to_dict(domain)})


@router.get("/domains/{domain_id}/dns-check", response_model=ApiResponse)
def check_domain_dns(
    request: Request,
    domain_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """检测域名的 MX / SPF / DMARC / DKIM 基础 DNS 配置。"""
    ensure_superadmin(admin)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    domain = db.get(MailDomain, _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    result = run_domain_dns_check(domain.name)
    record_admin_audit(
        request,
        admin,
        "admin.domains.dns_check",
        success=result["status"] != "error",
        target_type="domain",
        target_id=str(domain.id),
        metadata={"domain": domain.name, "status": result["status"]},
    )
    db.commit()
    return success_response(request, result)


@router.patch("/domains/{domain_id}", response_model=ApiResponse)
def update_domain(
    request: Request,
    domain_id: str,
    payload: DomainUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """更新域配置。"""
    ensure_superadmin(admin)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    domain = db.get(MailDomain, _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    if payload.name is not None:
        normalized_name = _normalize_domain_name(payload.name)
        if use_sqlite_mail_directory():
            renamed = rename_directory_domain(domain.name, normalized_name)
            domain.name = renamed.name
        else:
            existing = db.scalar(select(MailDomain).where(MailDomain.name == normalized_name, MailDomain.id != domain.id))
            if existing is not None:
                raise AppError("ADMIN_DOMAIN_EXISTS", "域名已存在", http_status=status.HTTP_400_BAD_REQUEST)
            domain.name = normalized_name
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
    """删除域并返回影响信息。"""
    ensure_superadmin(admin)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    domain = db.scalar(
        select(MailDomain)
        .where(MailDomain.id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
        .options(selectinload(MailDomain.accounts), selectinload(MailDomain.aliases))
    )
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    impact = {"user_count": len(domain.accounts or []), "alias_count": len(domain.aliases or [])}
    if use_sqlite_mail_directory():
        directory_impact = delete_directory_domain(domain.name)
        impact["user_count"] = directory_impact["account_count"]
        delete_shadow_domain(db, domain.name)
    else:
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
    """批量更新域状态。"""
    ensure_superadmin(admin)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    uuids = [_parse_uuid(item, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效") for item in payload.ids]
    domains = db.scalars(select(MailDomain).where(MailDomain.id.in_(uuids))).all()
    for domain in domains:
        domain.status = payload.status
    record_admin_audit(request, admin, "admin.domains.bulk_status", success=True, target_type="domain", metadata={"ids": payload.ids, "status": payload.status})
    db.commit()
    return success_response(request, {"updated": len(domains)})


@router.get("/queue", response_model=ApiResponse)
def admin_queue_list(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = None,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """查看 Postfix 队列当前状态。"""
    ensure_superadmin(admin)
    result = get_mail_queue_snapshot(status_filter=status_filter, q=q)
    record_admin_audit(
        request,
        admin,
        "admin.queue.list",
        success=result["status"] != "error",
        target_type="mail_queue",
        metadata={"status": result["status"], "total": result["summary"].get("total", 0)},
    )
    return success_response(request, result)


@router.get("/queue/{queue_id}", response_model=ApiResponse)
def admin_queue_detail(
    request: Request,
    queue_id: str,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """查看指定队列邮件正文。"""
    ensure_superadmin(admin)
    result = get_mail_queue_message(queue_id)
    record_admin_audit(
        request,
        admin,
        "admin.queue.detail",
        success=result["status"] == "ok",
        target_type="mail_queue",
        target_id=queue_id,
        metadata={"status": result["status"]},
    )
    return success_response(request, result)


@router.post("/queue/flush", response_model=ApiResponse)
def admin_queue_flush(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """触发 Postfix 队列 flush。"""
    ensure_superadmin(admin)
    result = flush_mail_queue()
    record_admin_audit(
        request,
        admin,
        "admin.queue.flush",
        success=result["status"] == "ok",
        target_type="mail_queue",
        metadata={"status": result["status"]},
    )
    return success_response(request, result)


@router.post("/queue/delete", response_model=ApiResponse)
def admin_queue_delete(
    request: Request,
    payload: QueueDeleteRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """删除指定队列邮件。"""
    ensure_superadmin(admin)
    result = delete_mail_queue_item(payload.queue_id)
    record_admin_audit(
        request,
        admin,
        "admin.queue.delete",
        success=result["status"] == "ok",
        target_type="mail_queue",
        target_id=payload.queue_id,
        metadata={"status": result["status"]},
    )
    return success_response(request, {"queue_id": payload.queue_id, **result})


@router.post("/queue/requeue", response_model=ApiResponse)
def admin_queue_requeue(
    request: Request,
    payload: QueueActionRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """重新投递指定队列邮件。"""
    ensure_superadmin(admin)
    result = requeue_mail_queue_item(payload.queue_id)
    record_admin_audit(
        request,
        admin,
        "admin.queue.requeue",
        success=result["status"] == "ok",
        target_type="mail_queue",
        target_id=payload.queue_id,
        metadata={"status": result["status"]},
    )
    return success_response(request, result)


@router.post("/queue/bulk-delete", response_model=ApiResponse)
def admin_queue_bulk_delete(
    request: Request,
    payload: QueueBulkDeleteRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """批量删除指定队列邮件。"""
    ensure_superadmin(admin)
    result = delete_mail_queue_items(payload.queue_ids)
    record_admin_audit(
        request,
        admin,
        "admin.queue.bulk_delete",
        success=result["status"] in {"ok", "partial"},
        target_type="mail_queue",
        metadata={"status": result["status"], "count": result["deleted_count"]},
    )
    return success_response(request, result)


@router.post("/queue/clear", response_model=ApiResponse)
def admin_queue_clear(
    request: Request,
    payload: QueueBulkDeleteRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """按状态清空队列。"""
    ensure_superadmin(admin)
    result = clear_mail_queue(statuses=payload.statuses)
    record_admin_audit(
        request,
        admin,
        "admin.queue.clear",
        success=result["status"] in {"ok", "partial"},
        target_type="mail_queue",
        metadata={"status": result["status"], "count": result["deleted_count"], "statuses": payload.statuses},
    )
    return success_response(request, result)


@router.get("/users", response_model=ApiResponse)
def list_admin_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = None,
    domain_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="email"),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """分页列出邮箱账号。"""
    page, page_size = normalize_pagination(page, page_size)
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
        directory_accounts = list_directory_accounts()
        shadow_accounts = db.scalars(select(MailAccount).options(selectinload(MailAccount.domain))).all()
        shadow_map = {item.email.lower(): item for item in shadow_accounts}
        visible_accounts: list[MailAccount] = []
        for directory_account in directory_accounts:
            shadow_account = shadow_map.get(directory_account.email.lower()) or ensure_shadow_account(
                db,
                directory_account.email,
                password_present=directory_account.password is not None,
            )
            if admin.role == "domain_admin" and admin.domain_id and shadow_account.domain_id != admin.domain_id:
                continue
            if domain_id and shadow_account.domain_id != _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"):
                continue
            if q:
                keyword = q.strip().lower()
                if keyword not in shadow_account.email.lower() and keyword not in (shadow_account.display_name or "").lower():
                    continue
            if status_filter and shadow_account.status != status_filter:
                continue
            visible_accounts.append(shadow_account)
        if sort == "-created_at":
            visible_accounts.sort(key=lambda item: item.created_at, reverse=True)
        elif sort == "created_at":
            visible_accounts.sort(key=lambda item: item.created_at)
        else:
            visible_accounts.sort(key=lambda item: item.email.lower())
        total = len(visible_accounts)
        items = visible_accounts[(page - 1) * page_size : page * page_size]
        _attach_account_usage(db, items)
        db.commit()
        return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[account_to_admin_dict(item) for item in items]))
    stmt = select(MailAccount).options(selectinload(MailAccount.domain))
    if admin.role == "domain_admin" and admin.domain_id:
        stmt = stmt.where(MailAccount.domain_id == admin.domain_id)
    elif domain_id:
        stmt = stmt.where(MailAccount.domain_id == _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if q:
        keyword = q.strip().lower()
        stmt = stmt.where(or_(func.lower(MailAccount.email).contains(keyword), func.lower(func.coalesce(MailAccount.display_name, "")).contains(keyword)))
    if status_filter:
        stmt = stmt.where(MailAccount.status == status_filter)
    if sort == "-created_at":
        stmt = stmt.order_by(MailAccount.created_at.desc())
    elif sort == "created_at":
        stmt = stmt.order_by(MailAccount.created_at.asc())
    else:
        stmt = stmt.order_by(MailAccount.email.asc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    _attach_account_usage(db, items)
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[account_to_admin_dict(item) for item in items]))


@router.post("/users", response_model=ApiResponse)
def create_admin_user_account(
    request: Request,
    payload: AdminUserCreateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """创建邮箱账号。"""
    if use_sqlite_mail_directory():
        domain = _resolve_target_domain(db, admin=admin, payload_domain_id=payload.domain_id, email=str(payload.email))
        _ensure_email_matches_domain(str(payload.email), domain)
        existing_directory = get_directory_account(str(payload.email))
        if existing_directory is not None:
            raise AppError("ADMIN_USER_EXISTS", "邮箱账号已存在", http_status=status.HTTP_400_BAD_REQUEST)
        directory_account = create_directory_account(str(payload.email), payload.password)
        shadow_account = ensure_shadow_account(db, directory_account.email, password_present=True)
        shadow_account.display_name = payload.display_name
        shadow_account.quota_mb = payload.quota_mb
        shadow_account.status = payload.status
        shadow_account.is_admin = payload.is_admin
        record_admin_audit(request, admin, "admin.users.create", success=True, target_type="mail_account", target_id=str(shadow_account.id), metadata={"email": shadow_account.email})
        db.commit()
        shadow_account = db.scalar(select(MailAccount).where(MailAccount.id == shadow_account.id).options(selectinload(MailAccount.domain)))
        if shadow_account is None:
            raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
        _attach_account_usage(db, [shadow_account])
        return success_response(request, {"user": account_to_admin_dict(shadow_account)})
    if db.scalar(select(MailAccount).where(func.lower(MailAccount.email) == payload.email.lower())):
        raise AppError("ADMIN_USER_EXISTS", "邮箱账号已存在", http_status=status.HTTP_400_BAD_REQUEST)
    domain = _resolve_target_domain(db, admin=admin, payload_domain_id=payload.domain_id, email=str(payload.email))
    _ensure_email_matches_domain(str(payload.email), domain)
    account = MailAccount(
        email=payload.email.lower(),
        display_name=payload.display_name,
        domain_id=domain.id if domain else None,
        password_hash=hash_mailbox_password(payload.password),
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
    account = db.scalar(select(MailAccount).where(MailAccount.id == account.id).options(selectinload(MailAccount.domain)))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    _attach_account_usage(db, [account])
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.get("/users/{user_id}", response_model=ApiResponse)
def get_admin_user_account(
    request: Request,
    user_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """获取单个邮箱账号详情。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    account = db.scalar(select(MailAccount).where(MailAccount.id == account.id).options(selectinload(MailAccount.domain)))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    _attach_account_usage(db, [account])
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.patch("/users/{user_id}", response_model=ApiResponse)
def update_admin_user_account(
    request: Request,
    user_id: str,
    payload: AdminUserUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """更新邮箱账号信息。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    if payload.domain_id is not None:
        next_domain = _resolve_target_domain(db, admin=admin, payload_domain_id=payload.domain_id)
        if next_domain is not None:
            _ensure_email_matches_domain(account.email, next_domain)
            account.domain_id = next_domain.id
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
    account = db.scalar(select(MailAccount).where(MailAccount.id == account.id).options(selectinload(MailAccount.domain)))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    _attach_account_usage(db, [account])
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.delete("/users/{user_id}", response_model=ApiResponse)
def delete_admin_user_account(
    request: Request,
    user_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """删除邮箱账号。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    if use_sqlite_mail_directory():
        delete_directory_account(account.email)
        delete_shadow_account(db, account.email)
    else:
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
    """重置邮箱账号密码。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    next_password = payload.password or (_generate_random_password() if payload.generate_random else None)
    if not next_password:
        raise AppError("ADMIN_PASSWORD_REQUIRED", "请提供新密码或启用随机生成", http_status=status.HTTP_400_BAD_REQUEST)
    if use_sqlite_mail_directory():
        update_directory_account_password(account.email, next_password)
        account.password_hash = None
    else:
        account.password_hash = hash_mailbox_password(next_password)
    record_admin_audit(request, admin, "admin.users.reset_password", success=True, target_type="mail_account", target_id=user_id, metadata={"password_changed": True, "generated": payload.generate_random})
    db.commit()
    return success_response(request, {"password_reset": True, "generated_password": next_password if payload.generate_random else None})


@router.post("/users/bulk-action", response_model=ApiResponse)
def admin_users_bulk_action(
    request: Request,
    payload: AdminUsersBulkActionRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """对邮箱账号执行批量状态或删除操作。"""
    if use_sqlite_mail_directory():
        sync_directory_shadow(db)
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
            if use_sqlite_mail_directory():
                delete_directory_account(account.email)
                delete_shadow_account(db, account.email)
            else:
                db.delete(account)
            changed += 1
    record_admin_audit(request, admin, "admin.users.bulk_action", success=True, target_type="mail_account", metadata={"action": payload.action, "count": changed})
    db.commit()
    return success_response(request, {"updated": changed})


@router.post("/users/import-csv", response_model=ApiResponse)
def import_admin_users_csv(
    request: Request,
    payload: UserCsvImportRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """批量导入邮箱用户。"""
    domain = None
    if payload.domain_id:
        domain = _resolve_target_domain(db, admin=admin, payload_domain_id=payload.domain_id)
    rows = _parse_user_csv(payload.csv_content)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        email = row["email"].lower()
        if use_sqlite_mail_directory():
            if get_directory_account(email) is not None:
                skipped.append({"email": email, "detail": "邮箱已存在"})
                continue
        elif db.scalar(select(MailAccount).where(func.lower(MailAccount.email) == email)):
            skipped.append({"email": email, "detail": "邮箱已存在"})
            continue
        target_domain = domain or _resolve_target_domain(db, admin=admin, payload_domain_id=None, email=email)
        _ensure_email_matches_domain(email, target_domain)
        if use_sqlite_mail_directory():
            directory_account = create_directory_account(email, row["password"])
            account = ensure_shadow_account(db, directory_account.email, password_present=True)
            account.display_name = row["display_name"] or None
            account.quota_mb = max(1, int(row["quota_mb"] or 500))
            account.status = row["status"] if row["status"] in {"active", "disabled"} else "active"
            account.is_admin = _parse_bool_text(row["is_admin"])
            created.append(account_to_admin_dict(account))
        else:
            account = MailAccount(
                email=email,
                display_name=row["display_name"] or None,
                domain_id=target_domain.id if target_domain else None,
                password_hash=hash_mailbox_password(row["password"]),
                quota_mb=max(1, int(row["quota_mb"] or 500)),
                status=row["status"] if row["status"] in {"active", "disabled"} else "active",
                is_admin=_parse_bool_text(row["is_admin"]),
                imap_host=get_settings().mail_imap_host,
                imap_port=get_settings().mail_imap_port,
                imap_ssl=get_settings().mail_imap_ssl,
                smtp_host=get_settings().mail_smtp_host,
                smtp_port=get_settings().mail_smtp_port,
                smtp_ssl=get_settings().mail_smtp_ssl,
            )
            db.add(account)
            db.flush()
            created.append(account_to_admin_dict(account))
    record_admin_audit(
        request,
        admin,
        "admin.users.import_csv",
        success=True,
        target_type="mail_account",
        metadata={"created": len(created), "skipped": len(skipped)},
    )
    db.commit()
    return success_response(request, {"created": len(created), "skipped": len(skipped), "items": created, "skipped_items": skipped})


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
    """分页列出邮件别名。"""
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(MailAlias).options(selectinload(MailAlias.domain))
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
    """创建邮件别名。"""
    domain_uuid = _parse_uuid(payload.domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效")
    ensure_domain_scope(admin, domain_uuid)
    domain = db.get(MailDomain, domain_uuid)
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    targets = _ensure_target_addresses([str(item) for item in payload.target_addresses])
    _ensure_alias_not_conflict(db, source_address=str(payload.source_address))
    _ensure_alias_no_direct_loop(str(payload.source_address), targets)
    _detect_alias_cycle(_alias_graph(db, domain_id=domain_uuid), str(payload.source_address), targets)
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
    """获取单个邮件别名详情。"""
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
    """更新邮件别名。"""
    alias = db.get(MailAlias, _parse_uuid(alias_id, code="ADMIN_ALIAS_INVALID_ID", message="别名 ID 无效"))
    if alias is None:
        raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, alias.domain_id)
    if payload.target_addresses is not None:
        targets = _ensure_target_addresses([str(item) for item in payload.target_addresses])
        _detect_alias_cycle(_alias_graph(db, domain_id=alias.domain_id), alias.source_address, targets)
        _ensure_alias_no_direct_loop(alias.source_address, targets)
        alias.target_addresses = targets
    if payload.is_active is not None:
        alias.is_active = payload.is_active
    record_admin_audit(request, admin, "admin.aliases.update", success=True, target_type="mail_alias", target_id=str(alias.id))
    db.commit()
    db.refresh(alias)
    return success_response(request, {"alias": alias_to_dict(alias)})


@router.post("/aliases/catch-all", response_model=ApiResponse)
def create_catch_all_alias(
    request: Request,
    payload: AliasCatchAllRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """一键创建 catch-all 别名。"""
    domain_uuid = _parse_uuid(payload.domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效")
    ensure_domain_scope(admin, domain_uuid)
    domain = db.get(MailDomain, domain_uuid)
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    source_address = f"@{domain.name}"
    targets = _ensure_target_addresses([str(payload.target_address)])
    _ensure_alias_not_conflict(db, source_address=source_address)
    _detect_alias_cycle(_alias_graph(db, domain_id=domain_uuid), source_address, targets)
    alias = MailAlias(domain_id=domain_uuid, source_address=source_address, target_addresses=targets, is_active=True)
    db.add(alias)
    db.flush()
    record_admin_audit(request, admin, "admin.aliases.catch_all_create", success=True, target_type="mail_alias", target_id=str(alias.id), metadata={"domain": domain.name, "target": str(payload.target_address)})
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
    """删除邮件别名。"""
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
    """切换邮件别名启用状态。"""
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
    q: str | None = None,
    domain_id: str | None = None,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """列出域配额使用情况。"""
    domains = db.scalars(select(MailDomain).options(selectinload(MailDomain.accounts), selectinload(MailDomain.quota_policy))).all()
    if admin.role == "domain_admin" and admin.domain_id:
        domains = [item for item in domains if item.id == admin.domain_id]
    elif domain_id:
        domain_uuid = _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效")
        domains = [item for item in domains if item.id == domain_uuid]
    all_accounts = [account for domain in domains for account in domain.accounts or []]
    usage_map = _attach_live_quota_usage(db, all_accounts)
    items: list[dict[str, Any]] = []
    for domain in domains:
        used_quota_mb = round(sum(usage_map.get(account.id, 0.0) for account in domain.accounts or []), 2)
        percentage = round((used_quota_mb / domain.quota_limit_mb) * 100, 2) if domain.quota_limit_mb else 0
        items.append(
            {
                **quota_policy_to_dict(domain.quota_policy, domain=domain),
                "quota_limit_mb": domain.quota_limit_mb,
                "used_quota_mb": used_quota_mb,
                "usage_percent": percentage,
                "status": quota_usage_status(percentage),
                "usage_source": "mixed" if any(getattr(account, "_usage_source", "").startswith("fallback:") for account in domain.accounts or []) else "doveadm",
            }
        )
    user_items = [
        account_to_admin_dict(account, used_quota_mb=usage_map.get(account.id, 0.0))
        for account in all_accounts
        if not q or q.strip().lower() in account.email.lower() or q.strip().lower() in (account.display_name or "").lower()
    ]
    return success_response(request, {"items": items, "user_items": user_items})


@router.patch("/quotas/policy", response_model=ApiResponse)
def update_quota_policy(
    request: Request,
    payload: QuotaPolicyUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """更新域配额策略。"""
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
    """更新单个邮箱账号配额。"""
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    account.quota_mb = payload.quota_mb
    record_admin_audit(request, admin, "admin.quotas.update_user", success=True, target_type="mail_account", target_id=str(account.id), metadata={"quota_mb": payload.quota_mb})
    db.commit()
    account = db.scalar(select(MailAccount).where(MailAccount.id == account.id).options(selectinload(MailAccount.domain)))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    usage_map = _attach_live_quota_usage(db, [account])
    return success_response(request, {"user": account_to_admin_dict(account)})


@router.post("/users/{user_id}/quota/recalc", response_model=ApiResponse)
def recalc_user_quota(
    request: Request,
    user_id: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """触发单个邮箱账号的真实配额重算。"""
    account = db.get(MailAccount, _parse_uuid(user_id, code="ADMIN_USER_INVALID_ID", message="用户 ID 无效"))
    if account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    ensure_domain_scope(admin, account.domain_id)
    result = recalc_mailbox_quota_usage(account.email)
    record_admin_audit(
        request,
        admin,
        "admin.quotas.recalc_user",
        success=result["status"] == "ok",
        target_type="mail_account",
        target_id=str(account.id),
        metadata={"email": account.email, "status": result["status"]},
    )
    refreshed_account = db.scalar(select(MailAccount).where(MailAccount.id == account.id).options(selectinload(MailAccount.domain)))
    if refreshed_account is None:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)
    _attach_live_quota_usage(db, [refreshed_account])
    return success_response(request, {"result": result, "user": account_to_admin_dict(refreshed_account)})


@router.post("/quotas/bulk-update", response_model=ApiResponse)
def bulk_update_quotas(
    request: Request,
    payload: QuotaBulkUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """批量更新邮箱账号配额。"""
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
    action: str | None = None,
    target: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """分页列出审计日志。"""
    ensure_superadmin(admin)
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(AuditLog)
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    if action:
        stmt = stmt.where(AuditLog.event_type.contains(action))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if target:
        stmt = stmt.where(or_(func.coalesce(AuditLog.target_id, "").contains(target), func.coalesce(AuditLog.target_type, "").contains(target)))
    if success_only is not None:
        stmt = stmt.where(AuditLog.success.is_(success_only))
    start_at, end_at = _parse_date_range(date_from, date_to)
    if start_at is not None:
        stmt = stmt.where(AuditLog.created_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(AuditLog.created_at <= end_at)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return success_response(request, paginate(page=page, page_size=page_size, total=total, items=[audit_log_to_dict(item) for item in items]))


@router.get("/action-history", response_model=ApiResponse)
def list_action_history(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    action_type: str | None = None,
    target_type: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """分页列出后台操作历史。"""
    ensure_superadmin(admin)
    page, page_size = normalize_pagination(page, page_size)
    stmt = select(AdminActionHistory)
    if action_type:
        stmt = stmt.where(AdminActionHistory.action_type.contains(action_type))
    if target_type:
        stmt = stmt.where(AdminActionHistory.target_type == target_type)
    if status_filter:
        stmt = stmt.where(AdminActionHistory.status == status_filter)
    stmt = stmt.order_by(AdminActionHistory.created_at.desc())
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return success_response(
        request,
        paginate(
            page=page,
            page_size=page_size,
            total=total,
            items=[
                {
                    "id": str(item.id),
                    "actor": item.admin_user_id and str(item.admin_user_id) or "system",
                    "action": item.action_type,
                    "target": item.target_id or item.target_type or "-",
                    "event_type": item.action_type,
                    "actor_type": "admin_user" if item.admin_user_id else "system",
                    "actor_id": str(item.admin_user_id) if item.admin_user_id else None,
                    "target_type": item.target_type,
                    "target_id": item.target_id,
                    "status": item.status,
                    "detail": item.detail,
                    "payload": item.payload,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
        ),
    )


@router.post("/audit-logs/export", response_model=ApiResponse)
def export_audit_logs(
    request: Request,
    payload: LogsExportRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """导出当前筛选条件下的审计日志。"""
    _ = admin
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(1000)
    if payload.q:
        stmt = stmt.where(AuditLog.event_type.contains(payload.q))
    if payload.status:
        stmt = stmt.where(AuditLog.event_type.contains(payload.status))
    items = [audit_log_to_dict(item) for item in db.scalars(stmt).all()]
    exported = export_log_items(
        [
            {
                "id": item["id"],
                "log_key": "audit",
                "label": "audit",
                "status": "success" if item.get("success", True) else "error",
                "source": item.get("actor_type") or "admin",
                "line_number": 0,
                "summary": item.get("action", ""),
                "raw": str(item),
            }
            for item in items
        ],
        format_name=payload.format,
    )
    record_admin_audit(
        request,
        admin,
        "admin.audit_logs.export",
        success=True,
        target_type="audit_log",
        metadata={"format": payload.format, "count": len(items)},
    )
    return success_response(request, exported)


@router.get("/logs", response_model=ApiResponse)
def admin_logs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    domain_id: str | None = None,
    sender: str | None = None,
    recipient: str | None = None,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """搜索邮件服务日志。"""
    ensure_superadmin(admin)
    result = search_mail_service_logs(
        log_key=domain_id or None,
        query=q,
        status_filter=status_filter,
        sender=sender,
        recipient=recipient,
    )
    page, page_size = normalize_pagination(page, page_size)
    items = result["items"]
    total = len(items)
    start_index = (page - 1) * page_size
    paged_items = items[start_index : start_index + page_size]
    payload = paginate(page=page, page_size=page_size, total=total, items=[
        {
            "id": item["id"],
            "source": item["log_key"],
            "level": status_filter or "info",
            "message": item["summary"],
            "created_at": datetime.now(UTC).isoformat(),
            "actor": item["label"],
            "target": item["source"],
        }
        for item in paged_items
    ])
    payload["updated_at"] = datetime.now(UTC).isoformat()
    payload["detail"] = result["detail"]
    return success_response(request, payload)


@router.post("/logs/export", response_model=ApiResponse)
def admin_logs_export(
    request: Request,
    payload: LogsExportRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """导出邮件服务日志。"""
    ensure_superadmin(admin)
    result = search_mail_service_logs(
        log_key=payload.log_key,
        query=payload.q,
        status_filter=payload.status,
        sender=payload.sender,
        recipient=payload.recipient,
    )
    exported = export_log_items(result["items"], format_name=payload.format)
    record_admin_audit(
        request,
        admin,
        "admin.logs.export",
        success=True,
        target_type="system_log",
        metadata={"format": payload.format, "count": len(result["items"])},
    )
    return success_response(request, exported)


@router.get("/system-health", response_model=ApiResponse)
@router.get("/system/health", response_model=ApiResponse, include_in_schema=False)
def admin_system_health(request: Request, admin: AdminContext = Depends(get_current_admin), db: Session = Depends(get_db_session)) -> dict[str, Any]:
    """返回后台系统健康检查结果。"""
    _ = admin
    now = datetime.now(UTC).isoformat()
    database_item = {
        "name": "database",
        "status": "ok" if db.execute(select(1)).scalar() == 1 else "down",
        "detail": "数据库连接正常",
    }
    try:
        redis_ok = bool(get_redis_client().ping())
        redis_item = {
            "name": "redis",
            "status": "ok" if redis_ok else "down",
            "detail": "Redis 连通正常" if redis_ok else "Redis 无响应",
        }
    except Exception as exc:
        redis_item = {
            "name": "redis",
            "status": "down",
            "detail": f"Redis 探测失败: {exc.__class__.__name__}",
        }
    application_item = {
        "name": "application",
        "status": "ok",
        "detail": f"应用健康检查时间 {now}",
    }
    service_items = list_service_health()
    disk_items = list_disk_usage()
    log_items = list_mail_service_logs()
    cpu_snapshot = get_cpu_usage_snapshot()
    memory_snapshot = get_memory_usage_snapshot()
    online_snapshot = get_online_dovecot_users()
    queue_snapshot = list_mail_queue()
    queue_alert = {
        "name": "queue_alert",
        "status": "warning" if int(queue_snapshot.get("summary", {}).get("deferred", 0) or 0) > 0 else "ok",
        "detail": f"deferred 队列 {queue_snapshot.get('summary', {}).get('deferred', 0)} 条",
    }
    cpu_item = {"name": "cpu", "status": cpu_snapshot["status"], "detail": cpu_snapshot["detail"]}
    memory_item = {"name": "memory", "status": memory_snapshot["status"], "detail": memory_snapshot["detail"]}
    online_item = {"name": "online_users", "status": online_snapshot["status"], "detail": online_snapshot["detail"]}
    ip_policy = get_admin_ip_policy()
    ip_item = {
        "name": "admin_ip_policy",
        "status": "ok",
        "detail": f"白名单 {len(ip_policy['allowlist'])} 项，黑名单 {len(ip_policy['blocklist'])} 项",
    }
    items = [database_item, redis_item, application_item, cpu_item, memory_item, online_item, queue_alert, ip_item, *service_items]
    return success_response(
        request,
        {
            "items": items,
            "services": service_items,
            "disks": disk_items,
            "logs": log_items,
            "checked_at": now,
            "cpu": cpu_snapshot,
            "memory": memory_snapshot,
            "online_users": online_snapshot,
            "queue": queue_snapshot,
            "ip_policy": ip_policy,
        },
    )


@router.get("/system-config", response_model=ApiResponse)
def admin_system_config(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """读取后台系统配置。"""
    ensure_superadmin(admin)
    setting = _get_or_create_system_setting(db)
    db.commit()
    return success_response(request, _system_setting_to_dict(setting))


@router.patch("/system-config", response_model=ApiResponse)
def update_admin_system_config(
    request: Request,
    payload: SystemConfigUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """更新后台系统配置。"""
    ensure_superadmin(admin)
    setting = _get_or_create_system_setting(db)
    setting.theme = payload.theme
    setting.language = payload.language
    setting.queue_auto_refresh_seconds = payload.queue_auto_refresh_seconds
    setting.queue_max_items = payload.queue_max_items
    setting.audit_default_days = payload.audit_default_days
    setting.log_retention_days = payload.log_retention_days
    setting.updated_by = admin.username
    cleanup_stats = cleanup_admin_logs(db, retention_days=payload.log_retention_days)
    _record_admin_action_history(
        db,
        admin=admin,
        action_type="system_config.update",
        status_value="ok",
        detail="已更新后台系统配置",
        target_type="admin_system_setting",
        payload={**_system_setting_to_dict(setting), "cleanup": cleanup_stats},
    )
    record_admin_audit(
        request,
        admin,
        "admin.system_config.update",
        success=True,
        target_type="admin_system_setting",
    )
    db.commit()
    db.refresh(setting)
    return success_response(request, {"config": _system_setting_to_dict(setting), "detail": "后台系统配置已保存", "cleanup": cleanup_stats})


@router.get("/mail-system/configs", response_model=ApiResponse)
def admin_mail_system_configs(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """读取 Postfix / Dovecot 配置预览。"""
    ensure_superadmin(admin)
    result = get_mail_system_configs()
    record_admin_audit(request, admin, "admin.mail_system.configs", success=result["status"] != "error", target_type="mail_system")
    return success_response(request, result)


@router.post("/mail-system/backup", response_model=ApiResponse)
def admin_mail_system_backup(
    request: Request,
    target_path: str,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """备份指定配置文件。"""
    ensure_superadmin(admin)
    result = backup_config_file(Path(target_path))
    _record_admin_action_history(
        db,
        admin=admin,
        action_type="mail_system.backup",
        status_value=result["status"],
        detail=str(result["detail"]),
        target_type="config_file",
        target_id=target_path,
        payload=result,
    )
    record_admin_audit(request, admin, "admin.mail_system.backup", success=result["status"] == "ok", target_type="config_file", target_id=target_path)
    db.commit()
    return success_response(request, result)


@router.post("/mail-system/restore", response_model=ApiResponse)
def admin_mail_system_restore(
    request: Request,
    payload: ConfigRestoreRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """从备份恢复配置。"""
    ensure_superadmin(admin)
    result = restore_config_backup(payload.backup_path, payload.target_path)
    _record_admin_action_history(
        db,
        admin=admin,
        action_type="mail_system.restore",
        status_value=result["status"],
        detail=str(result["detail"]),
        target_type="config_file",
        target_id=payload.target_path,
        payload=result,
    )
    record_admin_audit(request, admin, "admin.mail_system.restore", success=result["status"] == "ok", target_type="config_file", target_id=payload.target_path)
    db.commit()
    return success_response(request, result)


@router.post("/mail-system/postmap", response_model=ApiResponse)
def admin_mail_system_postmap(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """重建 Postfix 映射表。"""
    ensure_superadmin(admin)
    result = rebuild_postfix_maps()
    _record_admin_action_history(db, admin=admin, action_type="mail_system.postmap", status_value=result["status"], detail=str(result["detail"]), target_type="mail_system", payload=result)
    record_admin_audit(request, admin, "admin.mail_system.postmap", success=result["status"] == "ok", target_type="mail_system")
    db.commit()
    return success_response(request, result)


@router.post("/mail-system/postalias", response_model=ApiResponse)
def admin_mail_system_postalias(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """重建系统别名表。"""
    ensure_superadmin(admin)
    result = rebuild_system_aliases()
    _record_admin_action_history(db, admin=admin, action_type="mail_system.postalias", status_value=result["status"], detail=str(result["detail"]), target_type="mail_system", payload=result)
    record_admin_audit(request, admin, "admin.mail_system.postalias", success=result["status"] == "ok", target_type="mail_system")
    db.commit()
    return success_response(request, result)


@router.post("/mail-system/postfix/reload", response_model=ApiResponse)
def admin_mail_system_postfix_reload(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """重载 Postfix。"""
    ensure_superadmin(admin)
    result = reload_postfix_service()
    _record_admin_action_history(db, admin=admin, action_type="mail_system.postfix_reload", status_value=result["status"], detail=str(result["detail"]), target_type="service", target_id="postfix", payload=result)
    record_admin_audit(request, admin, "admin.mail_system.postfix_reload", success=result["status"] == "ok", target_type="service", target_id="postfix")
    db.commit()
    return success_response(request, result)


@router.post("/mail-system/dovecot/reload", response_model=ApiResponse)
def admin_mail_system_dovecot_reload(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """重载 Dovecot。"""
    ensure_superadmin(admin)
    result = reload_dovecot_service()
    _record_admin_action_history(db, admin=admin, action_type="mail_system.dovecot_reload", status_value=result["status"], detail=str(result["detail"]), target_type="service", target_id="dovecot", payload=result)
    record_admin_audit(request, admin, "admin.mail_system.dovecot_reload", success=result["status"] == "ok", target_type="service", target_id="dovecot")
    db.commit()
    return success_response(request, result)


@router.post("/system/service-action", response_model=ApiResponse)
def admin_service_action(
    request: Request,
    payload: ServiceActionRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """控制邮件服务启停。"""
    ensure_superadmin(admin)
    result = control_mail_service(payload.service, payload.action)
    _record_admin_action_history(
        db,
        admin=admin,
        action_type=f"service.{payload.action}",
        status_value=result["status"],
        detail=str(result["detail"]),
        target_type="service",
        target_id=payload.service,
        payload=result,
    )
    record_admin_audit(request, admin, "admin.system.service_action", success=result["status"] == "ok", target_type="service", target_id=payload.service, metadata={"action": payload.action})
    db.commit()
    return success_response(request, result)


@router.get("/rspamd", response_model=ApiResponse)
def admin_rspamd_overview(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """返回 Rspamd 全局阈值与域级 SPF / DMARC / DKIM 聚合结果。"""
    ensure_superadmin(admin)
    thresholds = get_rspamd_thresholds()
    domains = _load_rspamd_domains(admin, db)
    domain_items = [_build_rspamd_domain_payload(domain) for domain in domains]
    record_admin_audit(
        request,
        admin,
        "admin.rspamd.overview",
        success=thresholds["status"] != "error",
        target_type="rspamd",
        metadata={"domain_count": len(domain_items), "status": thresholds["status"]},
    )
    return success_response(
        request,
        {
            "thresholds": thresholds,
            "domains": domain_items,
        },
    )


@router.patch("/rspamd/thresholds", response_model=ApiResponse)
def admin_rspamd_update_thresholds(
    request: Request,
    payload: RspamdThresholdUpdateRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """更新 Rspamd 全局垃圾分阈值。"""
    ensure_superadmin(admin)
    result = update_rspamd_thresholds(
        {
            "reject": payload.reject,
            "add_header": payload.add_header,
            "greylist": payload.greylist,
        }
    )
    record_admin_audit(
        request,
        admin,
        "admin.rspamd.update_thresholds",
        success=result["status"] == "ok",
        target_type="rspamd",
        metadata={"status": result["status"], "thresholds": result["thresholds"]},
    )
    return success_response(request, result)


@router.post("/domains/{domain_id}/dkim/rotate", response_model=ApiResponse)
def admin_rotate_domain_dkim(
    request: Request,
    domain_id: str,
    payload: DkimRotateRequest,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """轮换指定域名的 DKIM 私钥。"""
    ensure_superadmin(admin)
    domain = db.get(MailDomain, _parse_uuid(domain_id, code="ADMIN_DOMAIN_INVALID_ID", message="域 ID 无效"))
    if domain is None:
        raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
    result = rotate_domain_dkim_key(domain.name, selector=payload.selector)
    record_admin_audit(
        request,
        admin,
        "admin.rspamd.rotate_dkim",
        success=result["status"] == "ok",
        target_type="domain",
        target_id=str(domain.id),
        metadata={"domain": domain.name, "status": result["status"], "selector": result.get("selector")},
    )
    return success_response(request, {"domain": domain.name, **result})


@router.get("/tls", response_model=ApiResponse)
def admin_tls_overview(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """返回当前证书状态列表。"""
    ensure_superadmin(admin)
    result = get_tls_certificates()
    record_admin_audit(
        request,
        admin,
        "admin.tls.overview",
        success=result["status"] != "error",
        target_type="tls",
        metadata={"status": result["status"], "count": len(result.get("items", []))},
    )
    return success_response(request, result)


@router.post("/tls/renew", response_model=ApiResponse)
def admin_tls_renew(
    request: Request,
    payload: TlsRenewRequest,
    admin: AdminContext = Depends(get_current_admin),
) -> dict[str, Any]:
    """触发 certbot renew。"""
    ensure_superadmin(admin)
    _ = payload
    result = renew_tls_certificates()
    record_admin_audit(
        request,
        admin,
        "admin.tls.renew",
        success=result["status"] == "ok",
        target_type="tls",
        metadata={"status": result["status"]},
    )
    return success_response(request, result)


@router.get("/overview", response_model=ApiResponse)
@router.get("/dashboard/overview", response_model=ApiResponse, include_in_schema=False)
def dashboard_overview(
    request: Request,
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """返回后台仪表盘概览数据。"""
    metrics = count_dashboard_metrics(db)
    queue_snapshot = list_mail_queue()
    recent_logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)).all()
    online_users = get_online_dovecot_users()
    return success_response(
        request,
        {
            "active_users": metrics["active_admin_total"],
            "mail_users": metrics["user_total"],
            "mail_domains": metrics["domain_total"],
            "aliases": metrics["alias_total"],
            "queued_jobs": queue_snapshot["summary"].get("total", 0),
            "summary": metrics,
            "recent_audits": [audit_log_to_dict(item) for item in recent_logs],
            "online_users": online_users,
            "queue_summary": queue_snapshot.get("summary", {}),
            "scope": {"role": admin.role, "domain_id": str(admin.domain_id) if admin.domain_id else None},
        },
    )


@router.get("/dashboard/trends", response_model=ApiResponse)
def dashboard_trends(
    request: Request,
    period: str = Query(default="7d", pattern="^(24h|7d|30d)$"),
    admin: AdminContext = Depends(get_current_admin),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """返回最近一段时间的审计与系统趋势数据。"""
    _ = admin
    today = utcnow().date()
    lookback_days = 1 if period == "24h" else (7 if period == "7d" else 30)
    points: list[dict[str, Any]] = []
    for offset in range(lookback_days - 1, -1, -1):
        day = today.fromordinal(today.toordinal() - offset)
        next_day = day.fromordinal(day.toordinal() + 1)
        sent_count = int(
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "compose.send_mail", AuditLog.created_at >= day, AuditLog.created_at < next_day)
            )
            or 0
        )
        admin_action_count = int(
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.actor_type == "admin_user", AuditLog.created_at >= day, AuditLog.created_at < next_day)
            )
            or 0
        )
        count = int(
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.created_at >= day, AuditLog.created_at < next_day)
            )
            or 0
        )
        points.append({"date": day.isoformat(), "audit_count": count, "sent_count": sent_count, "admin_action_count": admin_action_count})
    queue_snapshot = list_mail_queue()
    return success_response(request, {"period": period, "points": points, "queue_summary": queue_snapshot.get("summary", {})})
