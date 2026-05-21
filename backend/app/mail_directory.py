"""邮件目录适配层。

本模块统一封装“邮箱主数据目录”的访问逻辑。

当前支持两种模式：

- `postgres`：沿用应用自己的 PostgreSQL 模型
- `sqlite_vmail`：以 `/var/vmail/vmail.db` 为真实域/用户/密码来源

对于 `sqlite_vmail` 模式，这里只把域、邮箱账号和密码当作主数据；
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
from app.models import MailAccount, MailDomain


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
    params: list[Any] = []
    sql = "SELECT username, domain, password FROM accounts"
    if domain_name:
        sql += " WHERE domain = ?"
        params.append(_normalize_domain_name(domain_name))
    sql += " ORDER BY domain ASC, username ASC"
    with _connect_sqlite_directory() as connection:
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
            )
        )
    return items


def get_directory_account(email: str) -> DirectoryAccountRecord | None:
    """读取单个真实邮箱账号。"""
    username, domain = _split_email(email)
    with _connect_sqlite_directory() as connection:
        row = connection.execute(
            "SELECT username, domain, password FROM accounts WHERE username = ? AND domain = ?",
            (username, domain),
        ).fetchone()
    if row is None:
        return None
    return DirectoryAccountRecord(
        email=f"{username}@{domain}",
        username=username,
        domain=domain,
        password=str(row["password"]) if row["password"] is not None else None,
    )


def create_directory_account(email: str, password: str) -> DirectoryAccountRecord:
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
        connection.execute(
            "INSERT INTO accounts(username, domain, password) VALUES (?, ?, ?)",
            (username, domain, password),
        )
        connection.commit()
    return DirectoryAccountRecord(email=f"{username}@{domain}", username=username, domain=domain, password=password)


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
            quota_mb=500,
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
    setattr(account, "_has_directory_password", password_present)
    return account


def sync_directory_shadow(db: Session) -> dict[str, set[str]]:
    """把真实 vmail 目录中的域和账号同步到 PostgreSQL shadow records。"""
    if not use_sqlite_mail_directory():
        return {"domains": set(), "emails": set()}
    domains = list_directory_domains()
    accounts = list_directory_accounts()
    domain_names = {item.name for item in domains}
    emails = {item.email for item in accounts}
    for item in domains:
        ensure_shadow_domain(db, item.name)
    for item in accounts:
        ensure_shadow_account(db, item.email, password_present=item.password is not None)
    return {"domains": domain_names, "emails": emails}


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
