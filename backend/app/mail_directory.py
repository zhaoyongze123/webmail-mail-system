"""邮件目录适配层。

本模块统一封装“邮箱主数据目录”的访问逻辑。

当前支持两种模式：

- `postgres`：沿用应用自己的 PostgreSQL 模型
- `sqlite_vmail`：以 `/var/vmail/vmail.db` 为真实域/用户/密码来源

对于 `sqlite_vmail` 模式，这里只把域、邮箱账号、密码和别名当作主数据；
`status`、`display_name`、`quota_mb`、`is_admin` 等管理后台补充字段仍保存在
应用 PostgreSQL 的 shadow record 中，但不再作为邮箱主数据真源。
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.config as config
from app.errors import AppError
from app.models import MailAccount, MailAlias, MailDomain


@dataclass(frozen=True)
class DirectoryDomainRecord:
    """真实邮件目录中的域记录。"""

    name: str
    account_count: int


@dataclass(frozen=True)
class DirectoryAccountRecord:
    """真实邮件目录中的邮箱账号记录。"""

    email: str
    username: str
    domain: str
    password: str | None
    quota_mb: int | None = None


@dataclass(frozen=True)
class DirectoryAliasRecord:
    """真实邮件目录中的别名记录。"""

    source_address: str
    target_addresses: list[str]
    domain: str | None
    is_active: bool = True


def use_sqlite_mail_directory() -> bool:
    """返回当前是否启用了 vmail SQLite 目录模式。"""
    settings = config.get_settings()
    if hasattr(settings, "use_sqlite_mail_directory"):
        return bool(getattr(settings, "use_sqlite_mail_directory"))
    backend = str(getattr(settings, "mail_directory_backend", "postgres") or "postgres").strip().lower()
    return backend == "sqlite_vmail"


def _normalize_domain_name(name: str) -> str:
    """规范化域名文本。"""
    return name.strip().lower()


def _normalize_email(email: str) -> str:
    """规范化邮箱地址文本。"""
    return email.strip().lower()


def _split_email(email: str) -> tuple[str, str]:
    """拆分邮箱地址的用户名和域名。"""
    normalized = _normalize_email(email)
    if "@" not in normalized:
        raise AppError(
            "MAIL_DIRECTORY_INVALID_EMAIL",
            "邮箱格式不合法",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    username, domain = normalized.split("@", 1)
    if not username or not domain:
        raise AppError(
            "MAIL_DIRECTORY_INVALID_EMAIL",
            "邮箱格式不合法",
            http_status=status.HTTP_400_BAD_REQUEST,
        )
    return username, domain


def _connect_sqlite_directory() -> sqlite3.Connection:
    """连接真实 vmail SQLite 目录库。"""
    path = str(getattr(config.get_settings(), "mail_directory_sqlite_path", "/var/vmail/vmail.db"))
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise AppError(
            "MAIL_DIRECTORY_UNAVAILABLE",
            f"无法连接邮件目录数据库: {path}",
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    connection.row_factory = sqlite3.Row
    return connection


def _directory_has_table(connection: sqlite3.Connection, table_name: str) -> bool:
    """判断目录库里是否存在指定表。"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _directory_has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """判断目录库中的表是否包含指定字段。"""
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.Error:
        return False
    return any(str(row["name"] or "").strip().lower() == column_name.strip().lower() for row in rows)


def _ensure_directory_account_quota_column(connection: sqlite3.Connection) -> bool:
    """确保 accounts 表已经具备 quota_mb 字段。"""
    if _directory_has_column(connection, "accounts", "quota_mb"):
        return False
    connection.execute("ALTER TABLE accounts ADD COLUMN quota_mb INTEGER NOT NULL DEFAULT 500")
    return True


def list_directory_domains() -> list[DirectoryDomainRecord]:
    """读取真实邮件目录中的全部域。"""
    if not use_sqlite_mail_directory():
        return []
    with _connect_sqlite_directory() as connection:
        rows = connection.execute(
            """
            SELECT d.domain AS domain, COUNT(a.username) AS account_count
            FROM domains d
            LEFT JOIN accounts a ON a.domain = d.domain
            GROUP BY d.domain
            ORDER BY d.domain ASC
            """
        ).fetchall()
    return [
        DirectoryDomainRecord(
            name=_normalize_domain_name(str(row["domain"] or "")),
            account_count=int(row["account_count"] or 0),
        )
        for row in rows
        if str(row["domain"] or "").strip()
    ]


def get_directory_domain(name: str) -> DirectoryDomainRecord | None:
    """读取单个真实域记录。"""
    normalized = _normalize_domain_name(name)
    for item in list_directory_domains():
        if item.name == normalized:
            return item
    return None


def create_directory_domain(name: str) -> DirectoryDomainRecord:
    """在真实邮件目录中创建域。"""
    normalized = _normalize_domain_name(name)
    with _connect_sqlite_directory() as connection:
        existing = connection.execute("SELECT domain FROM domains WHERE domain = ?", (normalized,)).fetchone()
        if existing is not None:
            raise AppError("ADMIN_DOMAIN_EXISTS", "域名已存在", http_status=status.HTTP_400_BAD_REQUEST)
        connection.execute("INSERT INTO domains(domain) VALUES (?)", (normalized,))
        connection.commit()
    return DirectoryDomainRecord(name=normalized, account_count=0)


def rename_directory_domain(old_name: str, new_name: str) -> DirectoryDomainRecord:
    """在真实邮件目录中重命名域，并同步更新账号域字段。"""
    normalized_old = _normalize_domain_name(old_name)
    normalized_new = _normalize_domain_name(new_name)
    with _connect_sqlite_directory() as connection:
        existing = connection.execute("SELECT domain FROM domains WHERE domain = ?", (normalized_old,)).fetchone()
        if existing is None:
            raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
        duplicate = connection.execute("SELECT domain FROM domains WHERE domain = ?", (normalized_new,)).fetchone()
        if duplicate is not None and normalized_new != normalized_old:
            raise AppError("ADMIN_DOMAIN_EXISTS", "域名已存在", http_status=status.HTTP_400_BAD_REQUEST)
        connection.execute("UPDATE domains SET domain = ? WHERE domain = ?", (normalized_new, normalized_old))
        connection.execute("UPDATE accounts SET domain = ? WHERE domain = ?", (normalized_new, normalized_old))
        connection.commit()
        account_count = int(
            connection.execute("SELECT COUNT(*) FROM accounts WHERE domain = ?", (normalized_new,)).fetchone()[0] or 0
        )
    return DirectoryDomainRecord(name=normalized_new, account_count=account_count)


def delete_directory_domain(name: str) -> dict[str, int]:
    """删除真实邮件目录中的域及其账号。"""
    normalized = _normalize_domain_name(name)
    with _connect_sqlite_directory() as connection:
        domain_row = connection.execute("SELECT domain FROM domains WHERE domain = ?", (normalized,)).fetchone()
        if domain_row is None:
            raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
        account_count = int(
            connection.execute("SELECT COUNT(*) FROM accounts WHERE domain = ?", (normalized,)).fetchone()[0] or 0
        )
        connection.execute("DELETE FROM accounts WHERE domain = ?", (normalized,))
        connection.execute("DELETE FROM domains WHERE domain = ?", (normalized,))
        connection.commit()
    return {"account_count": account_count}


def list_directory_accounts(*, domain_name: str | None = None) -> list[DirectoryAccountRecord]:
    """读取真实邮件目录中的全部邮箱账号。"""
    if not use_sqlite_mail_directory():
        return []
    with _connect_sqlite_directory() as connection:
        has_quota_column = _directory_has_column(connection, "accounts", "quota_mb")
        params: list[Any] = []
        sql = "SELECT username, domain, password"
        if has_quota_column:
            sql += ", quota_mb"
        sql += " FROM accounts"
        if domain_name:
            sql += " WHERE domain = ?"
            params.append(_normalize_domain_name(domain_name))
        sql += " ORDER BY domain ASC, username ASC"
        rows = connection.execute(sql, tuple(params)).fetchall()
    items: list[DirectoryAccountRecord] = []
    for row in rows:
        username = str(row["username"] or "").strip().lower()
        domain = str(row["domain"] or "").strip().lower()
        if not username or not domain:
            continue
        items.append(
            DirectoryAccountRecord(
                email=f"{username}@{domain}",
                username=username,
                domain=domain,
                password=str(row["password"]) if row["password"] is not None else None,
                quota_mb=max(1, int(row["quota_mb"] or 500)) if "quota_mb" in row.keys() and row["quota_mb"] is not None else None,
            )
        )
    return items


def get_directory_account(email: str) -> DirectoryAccountRecord | None:
    """读取单个真实邮箱账号。"""
    username, domain = _split_email(email)
    with _connect_sqlite_directory() as connection:
        has_quota_column = _directory_has_column(connection, "accounts", "quota_mb")
        sql = "SELECT username, domain, password"
        if has_quota_column:
            sql += ", quota_mb"
        sql += " FROM accounts WHERE username = ? AND domain = ?"
        row = connection.execute(sql, (username, domain)).fetchone()
    if row is None:
        return None
    return DirectoryAccountRecord(
        email=f"{username}@{domain}",
        username=username,
        domain=domain,
        password=str(row["password"]) if row["password"] is not None else None,
        quota_mb=max(1, int(row["quota_mb"] or 500)) if "quota_mb" in row.keys() and row["quota_mb"] is not None else None,
    )


def list_directory_aliases(*, domain_name: str | None = None) -> list[DirectoryAliasRecord]:
    """读取真实邮件目录中的别名记录。"""
    if not use_sqlite_mail_directory():
        return []
    with _connect_sqlite_directory() as connection:
        if not _directory_has_table(connection, "aliases"):
            return []
        params: list[Any] = []
        sql = "SELECT source, destination, active, domain FROM aliases"
        if domain_name:
            sql += " WHERE lower(domain) = ?"
            params.append(_normalize_domain_name(domain_name))
        sql += " ORDER BY source ASC"
        rows = connection.execute(sql, tuple(params)).fetchall()
    items: list[DirectoryAliasRecord] = []
    for row in rows:
        source = str(row["source"] or "").strip().lower()
        destination = str(row["destination"] or "").strip()
        if not source or not destination:
            continue
        targets = [item.strip().lower() for item in destination.split(",") if item.strip()]
        items.append(
            DirectoryAliasRecord(
                source_address=source,
                target_addresses=targets,
                domain=str(row["domain"] or "").strip().lower() or None,
                is_active=bool(int(row["active"] or 1)) if str(row["active"] or "").strip() else True,
            )
        )
    return items


def alias_directory_capability() -> dict[str, object]:
    """返回真实目录是否支持别名读写。"""
    if not use_sqlite_mail_directory():
        return {
            "status": "ok",
            "detail": "当前使用 PostgreSQL 作为别名主数据源",
            "writable": True,
            "backend": "postgres",
        }
    with _connect_sqlite_directory() as connection:
        if not _directory_has_table(connection, "aliases"):
            return {
                "status": "unavailable",
                "detail": "当前 vmail.db 未启用 aliases 表，别名功能不可写",
                "writable": False,
                "backend": "sqlite_vmail",
            }
    return {
        "status": "ok",
        "detail": "当前 vmail.db 已启用 aliases 表",
        "writable": True,
        "backend": "sqlite_vmail",
    }


def get_directory_alias(source_address: str) -> DirectoryAliasRecord | None:
    """读取单个真实别名记录。"""
    normalized = _normalize_email(source_address)
    with _connect_sqlite_directory() as connection:
        if not _directory_has_table(connection, "aliases"):
            return None
        row = connection.execute(
            "SELECT source, destination, active, domain FROM aliases WHERE lower(source) = ?",
            (normalized,),
        ).fetchone()
    if row is None:
        return None
    destination = str(row["destination"] or "").strip()
    return DirectoryAliasRecord(
        source_address=normalized,
        target_addresses=[item.strip().lower() for item in destination.split(",") if item.strip()],
        domain=str(row["domain"] or "").strip().lower() or None,
        is_active=bool(int(row["active"] or 1)) if str(row["active"] or "").strip() else True,
    )


def _serialize_alias_targets(target_addresses: list[str]) -> str:
    """把别名目标地址序列化为目录库存储文本。"""
    return ",".join([item.strip().lower() for item in target_addresses if item.strip()])


def create_directory_alias(source_address: str, target_addresses: list[str], *, domain_name: str | None = None, is_active: bool = True) -> DirectoryAliasRecord:
    """在真实邮件目录中创建别名。"""
    normalized_source = _normalize_email(source_address)
    normalized_targets = [item.strip().lower() for item in target_addresses if item.strip()]
    if not normalized_targets:
        raise AppError("ADMIN_ALIAS_TARGET_REQUIRED", "别名必须至少包含一个目标地址", http_status=status.HTTP_400_BAD_REQUEST)
    with _connect_sqlite_directory() as connection:
        if not _directory_has_table(connection, "aliases"):
            raise AppError("ADMIN_ALIAS_UNAVAILABLE", "当前邮件目录未启用 aliases 表", http_status=status.HTTP_503_SERVICE_UNAVAILABLE)
        duplicate = connection.execute("SELECT source FROM aliases WHERE lower(source) = ?", (normalized_source,)).fetchone()
        if duplicate is not None:
            raise AppError("ADMIN_ALIAS_EXISTS", "别名已存在", http_status=status.HTTP_400_BAD_REQUEST)
        connection.execute(
            "INSERT INTO aliases(source, destination, active, domain) VALUES (?, ?, ?, ?)",
            (normalized_source, _serialize_alias_targets(normalized_targets), 1 if is_active else 0, (domain_name or "").strip().lower() or None),
        )
        connection.commit()
    return DirectoryAliasRecord(source_address=normalized_source, target_addresses=normalized_targets, domain=(domain_name or "").strip().lower() or None, is_active=is_active)


def update_directory_alias(source_address: str, target_addresses: list[str] | None = None, *, is_active: bool | None = None) -> DirectoryAliasRecord:
    """更新真实邮件目录中的别名。"""
    normalized_source = _normalize_email(source_address)
    with _connect_sqlite_directory() as connection:
        if not _directory_has_table(connection, "aliases"):
            raise AppError("ADMIN_ALIAS_UNAVAILABLE", "当前邮件目录未启用 aliases 表", http_status=status.HTTP_503_SERVICE_UNAVAILABLE)
        row = connection.execute("SELECT source, destination, active, domain FROM aliases WHERE lower(source) = ?", (normalized_source,)).fetchone()
        if row is None:
            raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)
        next_targets = [item.strip().lower() for item in str(row["destination"] or "").split(",") if item.strip()]
        if target_addresses is not None:
            next_targets = [item.strip().lower() for item in target_addresses if item.strip()]
        next_active = bool(int(row["active"] or 1)) if str(row["active"] or "").strip() else True
        if is_active is not None:
            next_active = is_active
        connection.execute(
            "UPDATE aliases SET destination = ?, active = ? WHERE lower(source) = ?",
            (_serialize_alias_targets(next_targets), 1 if next_active else 0, normalized_source),
        )
        connection.commit()
        domain = str(row["domain"] or "").strip().lower() or None
    return DirectoryAliasRecord(source_address=normalized_source, target_addresses=next_targets, domain=domain, is_active=next_active)


def delete_directory_alias(source_address: str) -> None:
    """删除真实邮件目录中的别名。"""
    normalized_source = _normalize_email(source_address)
    with _connect_sqlite_directory() as connection:
        if not _directory_has_table(connection, "aliases"):
            raise AppError("ADMIN_ALIAS_UNAVAILABLE", "当前邮件目录未启用 aliases 表", http_status=status.HTTP_503_SERVICE_UNAVAILABLE)
        cursor = connection.execute("DELETE FROM aliases WHERE lower(source) = ?", (normalized_source,))
        connection.commit()
    if int(cursor.rowcount or 0) <= 0:
        raise AppError("ADMIN_ALIAS_NOT_FOUND", "别名不存在", http_status=status.HTTP_404_NOT_FOUND)


def ensure_shadow_alias(db: Session, source_address: str, domain_name: str | None, target_addresses: list[str], *, is_active: bool = True) -> None:
    """确保 PostgreSQL 中存在对应的别名 shadow record。"""
    normalized_source = _normalize_email(source_address)
    domain = None
    if domain_name:
        domain = ensure_shadow_domain(db, domain_name)
    alias = db.scalar(select(MailAlias).where(MailAlias.source_address == normalized_source))
    if alias is None:
        alias = MailAlias(
            domain_id=domain.id if domain is not None else None,
            source_address=normalized_source,
            target_addresses=[item.strip().lower() for item in target_addresses if item.strip()],
            is_active=is_active,
        )
        db.add(alias)
        db.flush()
    else:
        if domain is not None:
            alias.domain_id = domain.id
        alias.target_addresses = [item.strip().lower() for item in target_addresses if item.strip()]
        alias.is_active = is_active
    return None


def delete_shadow_alias(db: Session, source_address: str) -> None:
    """删除 PostgreSQL 中对应的别名 shadow record。"""
    normalized_source = _normalize_email(source_address)
    alias = db.scalar(select(MailAlias).where(MailAlias.source_address == normalized_source))
    if alias is not None:
        db.delete(alias)


def create_directory_account(email: str, password: str, *, quota_mb: int = 500) -> DirectoryAccountRecord:
    """在真实邮件目录中创建邮箱账号。"""
    username, domain = _split_email(email)
    with _connect_sqlite_directory() as connection:
        domain_row = connection.execute("SELECT domain FROM domains WHERE domain = ?", (domain,)).fetchone()
        if domain_row is None:
            raise AppError("ADMIN_DOMAIN_NOT_FOUND", "域不存在", http_status=status.HTTP_404_NOT_FOUND)
        account_row = connection.execute(
            "SELECT username FROM accounts WHERE username = ? AND domain = ?",
            (username, domain),
        ).fetchone()
        if account_row is not None:
            raise AppError("ADMIN_USER_EXISTS", "邮箱账号已存在", http_status=status.HTTP_400_BAD_REQUEST)
        if _ensure_directory_account_quota_column(connection):
            connection.commit()
        if _directory_has_column(connection, "accounts", "quota_mb"):
            connection.execute(
                "INSERT INTO accounts(username, domain, password, quota_mb) VALUES (?, ?, ?, ?)",
                (username, domain, password, max(1, int(quota_mb or 500))),
            )
        else:
            connection.execute(
                "INSERT INTO accounts(username, domain, password) VALUES (?, ?, ?)",
                (username, domain, password),
            )
        connection.commit()
    return DirectoryAccountRecord(
        email=f"{username}@{domain}",
        username=username,
        domain=domain,
        password=password,
        quota_mb=max(1, int(quota_mb or 500)),
    )


def update_directory_account_password(email: str, password: str) -> None:
    """更新真实邮件目录中的邮箱密码。"""
    username, domain = _split_email(email)
    with _connect_sqlite_directory() as connection:
        cursor = connection.execute(
            "UPDATE accounts SET password = ? WHERE username = ? AND domain = ?",
            (password, username, domain),
        )
        connection.commit()
    if int(cursor.rowcount or 0) <= 0:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)


def update_directory_account_quota(email: str, quota_mb: int) -> None:
    """更新真实邮件目录中的邮箱配额。"""
    username, domain = _split_email(email)
    with _connect_sqlite_directory() as connection:
        if _ensure_directory_account_quota_column(connection):
            connection.commit()
        cursor = connection.execute(
            "UPDATE accounts SET quota_mb = ? WHERE username = ? AND domain = ?",
            (max(1, int(quota_mb or 500)), username, domain),
        )
        connection.commit()
    if int(cursor.rowcount or 0) <= 0:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)


def delete_directory_account(email: str) -> None:
    """删除真实邮件目录中的邮箱账号。"""
    username, domain = _split_email(email)
    with _connect_sqlite_directory() as connection:
        cursor = connection.execute(
            "DELETE FROM accounts WHERE username = ? AND domain = ?",
            (username, domain),
        )
        connection.commit()
    if int(cursor.rowcount or 0) <= 0:
        raise AppError("ADMIN_USER_NOT_FOUND", "邮箱账号不存在", http_status=status.HTTP_404_NOT_FOUND)


def ensure_shadow_domain(db: Session, domain_name: str) -> MailDomain:
    """确保 PostgreSQL 中存在对应的域 shadow record。"""
    normalized = _normalize_domain_name(domain_name)
    domain = db.scalar(select(MailDomain).where(MailDomain.name == normalized))
    if domain is None:
        domain = MailDomain(name=normalized, status="active")
        db.add(domain)
        db.flush()
    return domain


def ensure_shadow_account(
    db: Session,
    email: str,
    *,
    password_present: bool = True,
    quota_mb: int | None = None,
) -> MailAccount:
    """确保 PostgreSQL 中存在对应的账号 shadow record。"""
    normalized_email = _normalize_email(email)
    settings = config.get_settings()
    account = db.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
    _, domain_name = _split_email(normalized_email)
    shadow_domain = ensure_shadow_domain(db, domain_name)
    if account is None:
        account = MailAccount(
            email=normalized_email,
            domain_id=shadow_domain.id,
            password_hash=None,
            quota_mb=max(1, int(quota_mb or 500)),
            status="active",
            is_admin=False,
            imap_host=settings.mail_imap_host,
            imap_port=settings.mail_imap_port,
            imap_ssl=settings.mail_imap_ssl,
            smtp_host=settings.mail_smtp_host,
            smtp_port=settings.mail_smtp_port,
            smtp_ssl=settings.mail_smtp_ssl,
        )
        db.add(account)
        db.flush()
    else:
        account.domain_id = shadow_domain.id
        account.imap_host = settings.mail_imap_host
        account.imap_port = settings.mail_imap_port
        account.imap_ssl = settings.mail_imap_ssl
        account.smtp_host = settings.mail_smtp_host
        account.smtp_port = settings.mail_smtp_port
        account.smtp_ssl = settings.mail_smtp_ssl
    if quota_mb is not None:
        account.quota_mb = max(1, int(quota_mb or 500))
    setattr(account, "_has_directory_password", password_present)
    return account


def sync_directory_shadow(db: Session) -> dict[str, set[str]]:
    """把真实 vmail 目录中的域和账号同步到 PostgreSQL shadow records。"""
    if not use_sqlite_mail_directory():
        return {"domains": set(), "emails": set(), "aliases": set()}
    domains = list_directory_domains()
    accounts = list_directory_accounts()
    aliases = list_directory_aliases()
    domain_names = {item.name for item in domains}
    emails = {item.email for item in accounts}
    alias_sources = {item.source_address for item in aliases}
    for item in domains:
        ensure_shadow_domain(db, item.name)
    for item in accounts:
        ensure_shadow_account(
            db,
            item.email,
            password_present=item.password is not None,
            quota_mb=item.quota_mb,
        )
    shadow_aliases = db.scalars(select(MailAlias)).all()
    for item in aliases:
        ensure_shadow_alias(
            db,
            item.source_address,
            item.domain,
            item.target_addresses,
            is_active=item.is_active,
        )
    for shadow_alias in shadow_aliases:
        if shadow_alias.source_address not in alias_sources:
            delete_shadow_alias(db, shadow_alias.source_address)
    return {"domains": domain_names, "emails": emails, "aliases": alias_sources}


def delete_shadow_domain(db: Session, domain_name: str) -> None:
    """删除 PostgreSQL 中对应域的 shadow record。"""
    normalized = _normalize_domain_name(domain_name)
    domain = db.scalar(select(MailDomain).where(MailDomain.name == normalized))
    if domain is not None:
        db.delete(domain)


def delete_shadow_account(db: Session, email: str) -> None:
    """删除 PostgreSQL 中对应账号的 shadow record。"""
    normalized_email = _normalize_email(email)
    account = db.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
    if account is not None:
        db.delete(account)
