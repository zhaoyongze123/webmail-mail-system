from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID as UUIDType

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import get_session_factory
from app.models import MailAccount, MailFolder, MailMessage


logger = logging.getLogger("app.mail_state")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _folder_type(folder_name: str) -> str:
    normalized = folder_name.strip().lstrip(".").lower()
    if normalized == "inbox":
        return "inbox"
    if "sent" in normalized or "已发送" in normalized:
        return "sent"
    if "draft" in normalized or "草稿" in normalized:
        return "drafts"
    if "junk" in normalized or "spam" in normalized or "垃圾" in normalized:
        return "spam"
    if "trash" in normalized or "deleted" in normalized or "已删除" in normalized:
        return "trash"
    if "archive" in normalized or "归档" in normalized:
        return "archive"
    return "custom"


def _safe_db_write(operation: str, callback) -> Any | None:
    try:
        session_factory = get_session_factory()
        with session_factory() as db_session:
            result = callback(db_session)
            db_session.commit()
            return result
    except SQLAlchemyError as exc:
        logger.warning("邮件状态写入数据库失败 operation=%s error=%s", operation, exc.__class__.__name__)
    except Exception as exc:  # pragma: no cover - 数据库异常不应阻断邮箱主流程
        logger.warning("邮件状态写入数据库异常 operation=%s error=%s", operation, exc.__class__.__name__)
    return None


def ensure_mail_account(email: str, *, display_name: str | None = None) -> UUIDType | None:
    settings = get_settings()
    normalized_email = email.strip().lower()

    def write(db_session) -> UUIDType:
        account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
        if account is None:
            account = MailAccount(
                email=normalized_email,
                display_name=display_name,
                imap_host=settings.mail_imap_host,
                imap_port=settings.mail_imap_port,
                imap_ssl=settings.mail_imap_ssl,
                smtp_host=settings.mail_smtp_host,
                smtp_port=settings.mail_smtp_port,
                smtp_ssl=settings.mail_smtp_ssl,
            )
            db_session.add(account)
            db_session.flush()
            return account.id
        account.display_name = display_name or account.display_name
        account.imap_host = settings.mail_imap_host
        account.imap_port = settings.mail_imap_port
        account.imap_ssl = settings.mail_imap_ssl
        account.smtp_host = settings.mail_smtp_host
        account.smtp_port = settings.mail_smtp_port
        account.smtp_ssl = settings.mail_smtp_ssl
        account.updated_at = _now()
        return account.id

    return _safe_db_write("ensure_mail_account", write)


def sync_folders(email: str, folders: list[dict[str, Any]]) -> None:
    normalized_email = email.strip().lower()

    def write(db_session) -> None:
        account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
        if account is None:
            account_id = ensure_mail_account(normalized_email)
            if account_id is None:
                return
            account = db_session.get(MailAccount, account_id)
            if account is None:
                return
        for folder_data in folders:
            folder_name = str(folder_data["name"])
            folder = db_session.scalar(
                select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder_name)
            )
            if folder is None:
                folder = MailFolder(
                    account_id=account.id,
                    name=folder_name,
                    display_name=str(folder_data.get("display_name") or folder_name),
                    folder_type=str(folder_data.get("type") or _folder_type(folder_name)),
                )
                db_session.add(folder)
            folder.display_name = str(folder_data.get("display_name") or folder_name)
            folder.folder_type = str(folder_data.get("type") or _folder_type(folder_name))
            folder.delimiter = str(folder_data.get("delimiter") or "/")
            folder.uid_validity = int(folder_data["uid_validity"]) if folder_data.get("uid_validity") is not None else None
            folder.unread_count = int(folder_data.get("unread_count") or 0)
            folder.total_count = int(folder_data.get("total_count") or 0)
            folder.last_synced_at = _now()

    _safe_db_write("sync_folders", write)


def sync_message_summaries(email: str, folder_name: str, messages: list[dict[str, Any]], *, total_count: int | None = None) -> None:
    normalized_email = email.strip().lower()

    def write(db_session) -> None:
        account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
        if account is None:
            account_id = ensure_mail_account(normalized_email)
            if account_id is None:
                return
            account = db_session.get(MailAccount, account_id)
            if account is None:
                return
        folder = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder_name))
        if folder is None:
            folder = MailFolder(
                account_id=account.id,
                name=folder_name,
                display_name=folder_name,
                folder_type=_folder_type(folder_name),
                delimiter="/",
            )
            db_session.add(folder)
            db_session.flush()
        folder.total_count = total_count if total_count is not None else folder.total_count
        folder.unread_count = sum(1 for item in messages if not bool(item.get("read")))
        folder.last_synced_at = _now()
        for row in messages:
            uid = int(str(row["uid"]))
            message = db_session.scalar(
                select(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder.id,
                    MailMessage.imap_uid == uid,
                )
            )
            sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
            recipients = row.get("to") if isinstance(row.get("to"), list) else []
            recipient_emails = [str(item.get("email")) for item in recipients if isinstance(item, dict) and item.get("email")]
            sent_at = datetime.fromisoformat(str(row["date"])) if row.get("date") else None
            if message is None:
                message = MailMessage(account_id=account.id, folder_id=folder.id, imap_uid=uid)
                db_session.add(message)
            message.message_id = str(row.get("message_id") or "") or None
            message.subject = str(row.get("subject") or "")
            message.sender_name = str(sender.get("name") or "") or None
            message.sender_email = str(sender.get("email") or "") or None
            message.to_emails = recipient_emails
            message.sent_at = sent_at
            message.received_at = sent_at
            message.snippet = str(row.get("snippet") or "")
            message.has_attachments = bool(row.get("has_attachments"))
            message.is_read = bool(row.get("read"))
            message.cached_at = _now()

    _safe_db_write("sync_message_summaries", write)


def persist_message_read_state(email: str, folder_name: str, uids: list[str], *, is_read: bool) -> None:
    normalized_email = email.strip().lower()

    def write(db_session) -> None:
        account = db_session.scalar(select(MailAccount).where(MailAccount.email == normalized_email))
        if account is None:
            return
        folder = db_session.scalar(select(MailFolder).where(MailFolder.account_id == account.id, MailFolder.name == folder_name))
        if folder is None:
            return
        uid_values = [int(str(uid)) for uid in uids]
        messages = db_session.scalars(
            select(MailMessage).where(
                MailMessage.account_id == account.id,
                MailMessage.folder_id == folder.id,
                MailMessage.imap_uid.in_(uid_values),
            )
        ).all()
        for message in messages:
            message.is_read = is_read
            flags = list(message.flags or [])
            if is_read and "\\Seen" not in flags:
                flags.append("\\Seen")
            if not is_read:
                flags = [flag for flag in flags if flag != "\\Seen"]
            message.flags = flags
            message.cached_at = _now()
        folder.unread_count = int(
            db_session.scalar(
                select(func.count()).select_from(MailMessage).where(
                    MailMessage.account_id == account.id,
                    MailMessage.folder_id == folder.id,
                    MailMessage.is_read.is_(False),
                )
            )
            or 0
        )
        folder.last_synced_at = _now()

    _safe_db_write("persist_message_read_state", write)
