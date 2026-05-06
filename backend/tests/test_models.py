from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models import AuditLog, MailAccount, MailAttachment, MailDraft, MailFolder, MailMessage


def test_table_names() -> None:
    assert MailAccount.__tablename__ == "mail_accounts"
    assert MailFolder.__tablename__ == "mail_folders"
    assert MailMessage.__tablename__ == "mail_messages"
    assert MailDraft.__tablename__ == "mail_drafts"
    assert MailAttachment.__tablename__ == "mail_attachments"
    assert AuditLog.__tablename__ == "audit_logs"


def test_key_columns_exist() -> None:
    account_columns = set(MailAccount.__table__.columns.keys())
    folder_columns = set(MailFolder.__table__.columns.keys())
    message_columns = set(MailMessage.__table__.columns.keys())
    draft_columns = set(MailDraft.__table__.columns.keys())
    attachment_columns = set(MailAttachment.__table__.columns.keys())
    audit_columns = set(AuditLog.__table__.columns.keys())

    assert {"id", "email", "imap_host", "smtp_host", "created_at", "updated_at"} <= account_columns
    assert {"id", "account_id", "name", "type", "unread_count", "total_count"} <= folder_columns
    assert {"id", "account_id", "folder_id", "imap_uid", "to_emails", "flags", "cached_at"} <= message_columns
    assert {"id", "account_id", "subject", "attachment_refs", "status", "created_at"} <= draft_columns
    assert {"id", "account_id", "message_id", "draft_id", "storage_key", "expires_at"} <= attachment_columns
    assert {"id", "account_id", "event_type", "metadata", "created_at"} <= audit_columns


def test_message_unique_constraint_exists() -> None:
    constraints = [constraint for constraint in MailMessage.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    assert any(
        {column.name for column in constraint.columns} == {"account_id", "folder_id", "imap_uid"}
        for constraint in constraints
    )
