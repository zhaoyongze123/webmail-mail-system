"""add attachment preview index

Revision ID: 20260525_0010
Revises: 20260520_0009
Create Date: 2026-05-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260525_0010"
down_revision = "20260520_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_attachment_previews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("folder_name", sa.String(length=512), nullable=False),
        sa.Column("imap_uid", sa.BigInteger(), nullable=False),
        sa.Column("attachment_id", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("source_content_type", sa.String(length=255), nullable=False),
        sa.Column("preview_content_type", sa.String(length=255), nullable=True),
        sa.Column("preview_kind", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("preview_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["account_id"], ["mail_accounts.id"], name=op.f("fk_mail_attachment_previews_account_id_mail_accounts"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["mail_messages.id"], name=op.f("fk_mail_attachment_previews_message_id_mail_messages"), ondelete="CASCADE"),
        sa.UniqueConstraint("account_id", "folder_name", "imap_uid", "attachment_id", name=op.f("uq_mail_attachment_previews_account_id_folder_name_imap_uid_attachment_id")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_mail_attachment_previews_storage_key")),
    )
    op.create_index(op.f("ix_mail_attachment_previews_account_id"), "mail_attachment_previews", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_attachment_previews_message_id"), "mail_attachment_previews", ["message_id"], unique=False)
    op.create_index(op.f("ix_mail_attachment_previews_folder_name"), "mail_attachment_previews", ["folder_name"], unique=False)
    op.create_index(op.f("ix_mail_attachment_previews_imap_uid"), "mail_attachment_previews", ["imap_uid"], unique=False)
    op.create_index(op.f("ix_mail_attachment_previews_status"), "mail_attachment_previews", ["status"], unique=False)
    op.create_index(op.f("ix_mail_attachment_previews_last_accessed_at"), "mail_attachment_previews", ["last_accessed_at"], unique=False)
    op.create_index(op.f("ix_mail_attachment_previews_source_hash"), "mail_attachment_previews", ["source_hash"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_attachment_previews_source_hash"), table_name="mail_attachment_previews")
    op.drop_index(op.f("ix_mail_attachment_previews_last_accessed_at"), table_name="mail_attachment_previews")
    op.drop_index(op.f("ix_mail_attachment_previews_status"), table_name="mail_attachment_previews")
    op.drop_index(op.f("ix_mail_attachment_previews_imap_uid"), table_name="mail_attachment_previews")
    op.drop_index(op.f("ix_mail_attachment_previews_folder_name"), table_name="mail_attachment_previews")
    op.drop_index(op.f("ix_mail_attachment_previews_message_id"), table_name="mail_attachment_previews")
    op.drop_index(op.f("ix_mail_attachment_previews_account_id"), table_name="mail_attachment_previews")
    op.drop_table("mail_attachment_previews")
