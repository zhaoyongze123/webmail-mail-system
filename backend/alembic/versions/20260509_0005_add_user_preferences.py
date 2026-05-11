"""add user preferences table

Revision ID: 20260509_0005
Revises: 20260508_0004
Create Date: 2026-05-09 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260509_0005"
down_revision = "20260508_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_user_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mail_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_size", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("mark_read_on_open", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reply_quote_position", sa.String(length=20), nullable=False, server_default=sa.text("'bottom'")),
        sa.Column("language", sa.String(length=32), nullable=False, server_default=sa.text("'zh-CN'")),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default=sa.text("'Asia/Shanghai'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("account_id", name=op.f("uq_mail_user_preferences_account_id")),
    )
    op.create_index(op.f("ix_mail_user_preferences_account_id"), "mail_user_preferences", ["account_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_user_preferences_account_id"), table_name="mail_user_preferences")
    op.drop_table("mail_user_preferences")
