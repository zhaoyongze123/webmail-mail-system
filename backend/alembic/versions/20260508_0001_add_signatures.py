"""add signatures table

Revision ID: 20260508_0001
Revises: 20260506_0001
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260508_0001"
down_revision = "20260506_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_signatures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mail_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(op.f("ix_mail_signatures_account_id"), "mail_signatures", ["account_id"], unique=False)
    op.create_index(
        "uq_mail_signatures_account_default",
        "mail_signatures",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE"),
        sqlite_where=sa.text("is_default = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_mail_signatures_account_default", table_name="mail_signatures")
    op.drop_index(op.f("ix_mail_signatures_account_id"), table_name="mail_signatures")
    op.drop_table("mail_signatures")
