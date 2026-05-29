from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models import (
    AdminRefreshToken,
    AdminUser,
    AuditLog,
    MailAccount,
    MailAlias,
    MailAttachment,
    MailContact,
    MailContactTag,
    MailDomain,
    MailDraft,
    MailFolder,
    MailMessage,
    MailNotificationCursor,
    MailNotificationPreference,
    MailPushSubscription,
    MailSignature,
    MailUserPreference,
    QuotaPolicy,
)


def test_table_names() -> None:
    assert MailAccount.__tablename__ == "mail_accounts"
    assert MailFolder.__tablename__ == "mail_folders"
    assert MailMessage.__tablename__ == "mail_messages"
    assert MailDraft.__tablename__ == "mail_drafts"
    assert MailSignature.__tablename__ == "mail_signatures"
    assert MailUserPreference.__tablename__ == "mail_user_preferences"
    assert MailNotificationPreference.__tablename__ == "mail_notification_preferences"
    assert MailPushSubscription.__tablename__ == "mail_push_subscriptions"
    assert MailNotificationCursor.__tablename__ == "mail_notification_cursors"
    assert MailAttachment.__tablename__ == "mail_attachments"
    assert AuditLog.__tablename__ == "audit_logs"
    assert MailDomain.__tablename__ == "mail_domains"
    assert MailAlias.__tablename__ == "mail_aliases"
    assert QuotaPolicy.__tablename__ == "quota_policies"
    assert AdminUser.__tablename__ == "admin_users"
    assert AdminRefreshToken.__tablename__ == "admin_refresh_tokens"


def test_key_columns_exist() -> None:
    account_columns = set(MailAccount.__table__.columns.keys())
    folder_columns = set(MailFolder.__table__.columns.keys())
    message_columns = set(MailMessage.__table__.columns.keys())
    draft_columns = set(MailDraft.__table__.columns.keys())
    signature_columns = set(MailSignature.__table__.columns.keys())
    preference_columns = set(MailUserPreference.__table__.columns.keys())
    notification_preference_columns = set(MailNotificationPreference.__table__.columns.keys())
    push_subscription_columns = set(MailPushSubscription.__table__.columns.keys())
    notification_cursor_columns = set(MailNotificationCursor.__table__.columns.keys())
    contact_columns = set(MailContact.__table__.columns.keys())
    tag_columns = set(MailContactTag.__table__.columns.keys())
    attachment_columns = set(MailAttachment.__table__.columns.keys())
    audit_columns = set(AuditLog.__table__.columns.keys())
    domain_columns = set(MailDomain.__table__.columns.keys())
    alias_columns = set(MailAlias.__table__.columns.keys())
    quota_columns = set(QuotaPolicy.__table__.columns.keys())
    admin_user_columns = set(AdminUser.__table__.columns.keys())
    refresh_token_columns = set(AdminRefreshToken.__table__.columns.keys())

    assert {"id", "email", "domain_id", "password_hash", "quota_mb", "status", "is_admin", "imap_host", "smtp_host", "created_at", "updated_at"} <= account_columns
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
        "enabled",
        "permission_state",
        "mailbox_secret_encrypted",
        "last_error",
        "created_at",
        "updated_at",
    } <= notification_preference_columns
    assert {
        "id",
        "account_id",
        "endpoint",
        "endpoint_hash",
        "p256dh",
        "auth",
        "expiration_time",
        "user_agent",
        "last_seen_at",
        "created_at",
        "updated_at",
    } <= push_subscription_columns
    assert {
        "id",
        "account_id",
        "folder_name",
        "last_uid",
        "last_message_id",
        "last_checked_at",
        "created_at",
        "updated_at",
    } <= notification_cursor_columns
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
    assert {"id", "account_id", "event_type", "metadata", "actor_type", "actor_id", "target_type", "target_id", "created_at"} <= audit_columns
    assert {"id", "name", "quota_limit_mb", "status", "created_at", "updated_at"} <= domain_columns
    assert {"id", "domain_id", "source_address", "target_addresses", "is_active", "created_at", "updated_at"} <= alias_columns
    assert {"id", "domain_id", "default_quota_mb", "warn_80_enabled", "warn_90_enabled", "warn_95_enabled"} <= quota_columns
    assert {"id", "username", "password_hash", "role", "domain_id", "is_active", "totp_secret", "totp_enabled", "last_login_at"} <= admin_user_columns
    assert {"id", "admin_user_id", "token_hash", "expires_at", "revoked_at", "created_at"} <= refresh_token_columns


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


def test_notification_model_constraints_and_timezone_columns_are_valid() -> None:
    preference_constraints = [
        constraint
        for constraint in MailNotificationPreference.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    subscription_constraints = [
        constraint
        for constraint in MailPushSubscription.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    cursor_constraints = [
        constraint
        for constraint in MailNotificationCursor.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any({column.name for column in constraint.columns} == {"account_id"} for constraint in preference_constraints)
    assert any(
        {column.name for column in constraint.columns} == {"account_id", "endpoint"}
        for constraint in subscription_constraints
    )
    assert any(
        {column.name for column in constraint.columns} == {"account_id", "folder_name"}
        for constraint in cursor_constraints
    )
    assert MailNotificationPreference.__table__.c.created_at.type.timezone is True
    assert MailNotificationPreference.__table__.c.updated_at.type.timezone is True
    assert MailPushSubscription.__table__.c.created_at.type.timezone is True
    assert MailPushSubscription.__table__.c.updated_at.type.timezone is True
    assert MailNotificationCursor.__table__.c.created_at.type.timezone is True
    assert MailNotificationCursor.__table__.c.updated_at.type.timezone is True


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


def test_admin_related_constraints_exist() -> None:
    domain_constraints = [constraint for constraint in MailDomain.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    alias_constraints = [constraint for constraint in MailAlias.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    admin_constraints = [constraint for constraint in AdminUser.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    refresh_constraints = [constraint for constraint in AdminRefreshToken.__table__.constraints if isinstance(constraint, UniqueConstraint)]
    quota_constraints = [constraint for constraint in QuotaPolicy.__table__.constraints if isinstance(constraint, UniqueConstraint)]

    assert any({column.name for column in constraint.columns} == {"name"} for constraint in domain_constraints)
    assert any({column.name for column in constraint.columns} == {"source_address"} for constraint in alias_constraints)
    assert any({column.name for column in constraint.columns} == {"username"} for constraint in admin_constraints)
    assert any({column.name for column in constraint.columns} == {"token_hash"} for constraint in refresh_constraints)
    assert any({column.name for column in constraint.columns} == {"domain_id"} for constraint in quota_constraints)
