from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models import AuditLog, MailAccount, MailAttachment, MailContact, MailContactTag, MailDraft, MailFolder, MailMessage, MailSignature, MailUserPreference


def test_table_names() -> None:
    assert MailAccount.__tablename__ == "mail_accounts"
    assert MailFolder.__tablename__ == "mail_folders"
    assert MailMessage.__tablename__ == "mail_messages"
    assert MailDraft.__tablename__ == "mail_drafts"
    assert MailSignature.__tablename__ == "mail_signatures"
    assert MailUserPreference.__tablename__ == "mail_user_preferences"
    assert MailAttachment.__tablename__ == "mail_attachments"
    assert AuditLog.__tablename__ == "audit_logs"


def test_key_columns_exist() -> None:
    account_columns = set(MailAccount.__table__.columns.keys())
    folder_columns = set(MailFolder.__table__.columns.keys())
    message_columns = set(MailMessage.__table__.columns.keys())
    draft_columns = set(MailDraft.__table__.columns.keys())
    signature_columns = set(MailSignature.__table__.columns.keys())
    preference_columns = set(MailUserPreference.__table__.columns.keys())
    contact_columns = set(MailContact.__table__.columns.keys())
    tag_columns = set(MailContactTag.__table__.columns.keys())
    attachment_columns = set(MailAttachment.__table__.columns.keys())
    audit_columns = set(AuditLog.__table__.columns.keys())

    assert {"id", "email", "imap_host", "smtp_host", "created_at", "updated_at"} <= account_columns
    assert {"id", "account_id", "name", "type", "unread_count", "total_count"} <= folder_columns
    assert {"id", "account_id", "folder_id", "imap_uid", "to_emails", "flags", "cached_at"} <= message_columns
    assert {"id", "account_id", "subject", "attachment_refs", "status", "created_at"} <= draft_columns
    assert {"id", "account_id", "name", "content", "is_default", "created_at"} <= signature_columns
    assert {
        "id",
        "account_id",
        "page_size",
        "mark_read_on_open",
        "reply_quote_position",
        "language",
        "timezone",
        "display_name",
        "profile_title",
        "avatar_url",
        "bio",
        "theme_mode",
        "created_at",
        "updated_at",
    } <= preference_columns
    assert {
        "id",
        "account_id",
        "email",
        "display_name",
        "group_name",
        "company",
        "phone",
        "notes",
        "is_favorite",
        "is_blacklisted",
        "is_whitelisted",
        "source",
        "use_count",
        "last_used_at",
        "created_at",
        "updated_at",
    } <= contact_columns
    assert {"id", "contact_id", "name", "created_at"} <= tag_columns
    assert {"id", "account_id", "message_id", "draft_id", "storage_key", "expires_at"} <= attachment_columns
    assert {"id", "account_id", "event_type", "metadata", "created_at"} <= audit_columns


def test_message_unique_constraint_exists() -> None:
    constraints = [constraint for constraint in MailMessage.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    assert any(
        {column.name for column in constraint.columns} == {"account_id", "folder_id", "imap_uid"}
        for constraint in constraints
    )


def test_signature_default_partial_index_exists() -> None:
    assert any(
        index.name == "uq_mail_signatures_account_default" and index.unique
        for index in MailSignature.__table__.indexes
    )


def test_account_updated_at_is_configured_for_updates() -> None:
    assert MailAccount.__table__.c.updated_at.onupdate is not None
    assert MailDraft.__table__.c.updated_at.onupdate is not None
    assert MailSignature.__table__.c.updated_at.onupdate is not None
    assert MailUserPreference.__table__.c.updated_at.onupdate is not None
    assert MailContact.__table__.c.updated_at.onupdate is not None


def test_mail_signature_timestamp_columns_are_timezone_aware() -> None:
    assert MailSignature.__table__.c.created_at.type.timezone is True
    assert MailSignature.__table__.c.updated_at.type.timezone is True


def test_mail_user_preference_constraints_and_timezone_columns_are_valid() -> None:
    constraints = [constraint for constraint in MailUserPreference.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    assert any(
        {column.name for column in constraint.columns} == {"account_id"}
        for constraint in constraints
    )
    assert MailUserPreference.__table__.c.created_at.type.timezone is True
    assert MailUserPreference.__table__.c.updated_at.type.timezone is True


def test_contact_unique_constraint_and_tag_indexes_exist() -> None:
    contact_constraints = [constraint for constraint in MailContact.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    assert any(
        {column.name for column in constraint.columns} == {"account_id", "email"}
        for constraint in contact_constraints
    )
    tag_constraints = [constraint for constraint in MailContactTag.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    assert any(
        {column.name for column in constraint.columns} == {"contact_id", "name"}
        for constraint in tag_constraints
    )
    assert any(index.name == "ix_mail_contacts_account_id_last_used_at" for index in MailContact.__table__.indexes)
    assert any(index.name == "ix_mail_contacts_account_id_is_blacklisted" for index in MailContact.__table__.indexes)
    assert any(index.name == "ix_mail_contact_tags_contact_id" for index in MailContactTag.__table__.indexes)
    assert any(index.name == "ix_mail_contact_tags_name" for index in MailContactTag.__table__.indexes)
