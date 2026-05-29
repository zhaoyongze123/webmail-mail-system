"""add mail push notifications

Revision ID: 20260525_0011
Revises: 20260525_0010
Create Date: 2026-05-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260525_0011"
down_revision = "20260525_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("permission_state", sa.String(length=20), nullable=False, server_default=sa.text("'default'")),
        sa.Column("mailbox_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_mail_notification_preferences_account_id_mail_accounts"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("account_id", name=op.f("uq_mail_notification_preferences_account_id")),
    )
    op.create_index(op.f("ix_mail_notification_preferences_account_id"), "mail_notification_preferences", ["account_id"], unique=False)

    op.create_table(
        "mail_push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=128), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("expiration_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_mail_push_subscriptions_account_id_mail_accounts"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("account_id", "endpoint", name=op.f("uq_mail_push_subscriptions_account_id_endpoint")),
    )
    op.create_index(op.f("ix_mail_push_subscriptions_account_id"), "mail_push_subscriptions", ["account_id"], unique=False)
    op.create_index(op.f("ix_mail_push_subscriptions_endpoint_hash"), "mail_push_subscriptions", ["endpoint_hash"], unique=False)

    op.create_table(
        "mail_notification_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_name", sa.String(length=512), nullable=False, server_default=sa.text("'INBOX'")),
        sa.Column("last_uid", sa.BigInteger(), nullable=True),
        sa.Column("last_message_id", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["mail_accounts.id"],
            name=op.f("fk_mail_notification_cursors_account_id_mail_accounts"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("account_id", "folder_name", name=op.f("uq_mail_notification_cursors_account_id_folder_name")),
    )
    op.create_index(op.f("ix_mail_notification_cursors_account_id"), "mail_notification_cursors", ["account_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_notification_cursors_account_id"), table_name="mail_notification_cursors")
    op.drop_table("mail_notification_cursors")
    op.drop_index(op.f("ix_mail_push_subscriptions_endpoint_hash"), table_name="mail_push_subscriptions")
    op.drop_index(op.f("ix_mail_push_subscriptions_account_id"), table_name="mail_push_subscriptions")
    op.drop_table("mail_push_subscriptions")
    op.drop_index(op.f("ix_mail_notification_preferences_account_id"), table_name="mail_notification_preferences")
    op.drop_table("mail_notification_preferences")
