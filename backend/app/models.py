"""数据库模型定义。

这里集中维护 Webmail 用户态模型与后台一期新增管理态模型，是查询、
迁移和关系建模的统一事实源。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as UUIDType, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


JSON_COMPAT = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")
INET_COMPAT = String(45).with_variant(INET(), "postgresql")


class MailAccount(Base):
    """邮箱账号主表。"""

    __tablename__ = "mail_accounts"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    domain_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_domains.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quota_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imap_host: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False)
    imap_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    domain: Mapped["MailDomain | None"] = relationship(back_populates="accounts")
    folders: Mapped[list["MailFolder"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list["MailMessage"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    drafts: Mapped[list["MailDraft"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    signatures: Mapped[list["MailSignature"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    preferences: Mapped["MailUserPreference | None"] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    notification_preference: Mapped["MailNotificationPreference | None"] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )
    notification_subscriptions: Mapped[list["MailPushSubscription"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    notification_cursors: Mapped[list["MailNotificationCursor"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    contacts: Mapped[list["MailContact"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["MailAttachment"]] = relationship(back_populates="account")
    attachment_previews: Mapped[list["MailAttachmentPreview"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="account")


class MailDomain(Base):
    """邮件域表。"""

    __tablename__ = "mail_domains"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    quota_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=10240)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    accounts: Mapped[list["MailAccount"]] = relationship(back_populates="domain")
    aliases: Mapped[list["MailAlias"]] = relationship(
        back_populates="domain",
        cascade="all, delete-orphan",
    )
    quota_policy: Mapped["QuotaPolicy | None"] = relationship(
        back_populates="domain",
        cascade="all, delete-orphan",
        uselist=False,
    )
    admin_users: Mapped[list["AdminUser"]] = relationship(back_populates="domain")


class MailFolder(Base):
    """邮箱文件夹快照表。"""

    __tablename__ = "mail_folders"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_type: Mapped[str] = mapped_column("type", String(50), nullable=False)
    delimiter: Mapped[str | None] = mapped_column(String(10), nullable=True)
    uid_validity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped["MailAccount"] = relationship(back_populates="folders")
    messages: Mapped[list["MailMessage"]] = relationship(
        back_populates="folder",
        cascade="all, delete-orphan",
    )


class MailMessage(Base):
    """邮件摘要与状态缓存表。"""

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("account_id", "folder_id", "imap_uid"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_folders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    imap_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    to_emails: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    cc_emails: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="messages")
    folder: Mapped["MailFolder"] = relationship(back_populates="messages")
    attachments: Mapped[list["MailAttachment"]] = relationship(back_populates="message")
    attachment_previews: Mapped[list["MailAttachmentPreview"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )


class MailDraft(Base):
    """草稿表。"""

    __tablename__ = "mail_drafts"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    imap_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_emails: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    cc_emails: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    bcc_emails: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_refs: Mapped[list[dict]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="editing")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="drafts")
    attachments: Mapped[list["MailAttachment"]] = relationship(back_populates="draft")


class MailSignature(Base):
    """签名表。"""

    __tablename__ = "mail_signatures"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="signatures")


Index(
    "uq_mail_signatures_account_default",
    MailSignature.account_id,
    unique=True,
    postgresql_where=MailSignature.is_default.is_(True),
    sqlite_where=MailSignature.is_default.is_(True),
)


class MailUserPreference(Base):
    """用户偏好设置表。"""

    __tablename__ = "mail_user_preferences"
    __table_args__ = (
        UniqueConstraint("account_id"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_size: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    mark_read_on_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reply_quote_position: Mapped[str] = mapped_column(String(20), nullable=False, default="bottom")
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="zh-CN")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    profile_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    avatar_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    theme_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="light")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="preferences")


class MailNotificationPreference(Base):
    """邮箱账号的新邮件通知偏好。"""

    __tablename__ = "mail_notification_preferences"
    __table_args__ = (
        UniqueConstraint("account_id"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permission_state: Mapped[str] = mapped_column(String(20), nullable=False, default="default")
    mailbox_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="notification_preference")


class MailPushSubscription(Base):
    """浏览器 Web Push 订阅记录。"""

    __tablename__ = "mail_push_subscriptions"
    __table_args__ = (
        UniqueConstraint("account_id", "endpoint"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    expiration_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="notification_subscriptions")


class MailNotificationCursor(Base):
    """通知轮询的邮箱游标状态。"""

    __tablename__ = "mail_notification_cursors"
    __table_args__ = (
        UniqueConstraint("account_id", "folder_name"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder_name: Mapped[str] = mapped_column(String(512), nullable=False, default="INBOX")
    last_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="notification_cursors")


class MailContact(Base):
    """通讯录联系人实体，保存联系人属性与黑白名单状态。"""

    __tablename__ = "mail_contacts"
    __table_args__ = (
        UniqueConstraint("account_id", "email"),
        Index("ix_mail_contacts_account_id_last_used_at", "account_id", "last_used_at"),
        Index("ix_mail_contacts_account_id_is_blacklisted", "account_id", "is_blacklisted"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="contacts")
    tags: Mapped[list["MailContactTag"]] = relationship(
        back_populates="contact",
        cascade="all, delete-orphan",
        order_by="MailContactTag.name",
    )


class MailContactTag(Base):
    """联系人标签实体，用于给联系人打分类标签。"""

    __tablename__ = "mail_contact_tags"
    __table_args__ = (
        UniqueConstraint("contact_id", "name"),
        Index("ix_mail_contact_tags_name", "name"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    contact_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    contact: Mapped["MailContact"] = relationship(back_populates="tags")


class MailAttachment(Base):
    """附件元数据实体，记录临时附件或已落库附件的存储信息。"""

    __tablename__ = "mail_attachments"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    draft_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="temp")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped["MailAccount"] = relationship(back_populates="attachments")
    message: Mapped["MailMessage | None"] = relationship(back_populates="attachments")
    draft: Mapped["MailDraft | None"] = relationship(back_populates="attachments")


class MailAttachmentPreview(Base):
    """附件预览索引实体，只在数据库中保存索引元数据，不保存大文件本体。"""

    __tablename__ = "mail_attachment_previews"
    __table_args__ = (
        UniqueConstraint("account_id", "folder_name", "imap_uid", "attachment_id"),
        Index("ix_mail_attachment_previews_status", "status"),
        Index("ix_mail_attachment_previews_last_accessed_at", "last_accessed_at"),
        Index("ix_mail_attachment_previews_source_hash", "source_hash"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    folder_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    imap_uid: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    attachment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    source_content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    preview_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preview_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    preview_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped["MailAccount"] = relationship(back_populates="attachment_previews")
    message: Mapped["MailMessage | None"] = relationship(back_populates="attachment_previews")


class AuditLog(Base):
    """审计日志实体，记录用户端和后台端的关键操作轨迹。"""

    __tablename__ = "audit_logs"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET_COMPAT, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict | list | None] = mapped_column("metadata", JSON_COMPAT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped["MailAccount | None"] = relationship(back_populates="audit_logs")


class MailAlias(Base):
    """域名别名实体，描述源地址到多个目标地址的映射关系。"""

    __tablename__ = "mail_aliases"
    __table_args__ = (
        UniqueConstraint("source_address"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    domain_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_address: Mapped[str] = mapped_column(String(320), nullable=False)
    target_addresses: Mapped[list[str]] = mapped_column(JSON_COMPAT, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    domain: Mapped["MailDomain"] = relationship(back_populates="aliases")


class QuotaPolicy(Base):
    """配额策略实体，支持平台级或域级默认邮箱配额配置。"""

    __tablename__ = "quota_policies"
    __table_args__ = (
        UniqueConstraint("domain_id"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    domain_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_domains.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    default_quota_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    warn_80_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    warn_90_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    warn_95_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    domain: Mapped["MailDomain | None"] = relationship(back_populates="quota_policy")


class AdminUser(Base):
    """后台管理员实体，支持平台管理员和域管理员两级角色。"""

    __tablename__ = "admin_users"
    __table_args__ = (
        UniqueConstraint("username"),
    )

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="superadmin")
    domain_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mail_domains.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    domain: Mapped["MailDomain | None"] = relationship(back_populates="admin_users")
    refresh_tokens: Mapped[list["AdminRefreshToken"]] = relationship(
        back_populates="admin_user",
        cascade="all, delete-orphan",
    )


class AdminRefreshToken(Base):
    """后台刷新令牌实体，用于维护管理员长期会话。"""

    __tablename__ = "admin_refresh_tokens"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    admin_user_id: Mapped[UUIDType] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    admin_user: Mapped["AdminUser"] = relationship(back_populates="refresh_tokens")


class AdminSystemSetting(Base):
    """后台系统配置实体，保存主题、语言和运维页默认参数。"""

    __tablename__ = "admin_system_settings"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    theme: Mapped[str] = mapped_column(String(20), nullable=False, default="system")
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="zh-CN")
    queue_auto_refresh_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    queue_max_items: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    audit_default_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    log_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AdminActionHistory(Base):
    """后台危险操作执行历史实体。"""

    __tablename__ = "admin_action_history"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    admin_user_id: Mapped[UUIDType | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | list | None] = mapped_column(JSON_COMPAT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
