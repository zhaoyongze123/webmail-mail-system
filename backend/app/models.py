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
    __tablename__ = "mail_accounts"

    id: Mapped[UUIDType] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    contacts: Mapped[list["MailContact"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["MailAttachment"]] = relationship(back_populates="account")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="account")


class MailFolder(Base):
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


class MailDraft(Base):
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


class MailContact(Base):
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


class AuditLog(Base):
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
