"""add contact blacklist flag

Revision ID: 20260508_0003
Revises: 20260508_0002
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260508_0003"
down_revision = "20260508_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_contacts",
        sa.Column("is_blacklisted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_mail_contacts_account_id_is_blacklisted",
        "mail_contacts",
        ["account_id", "is_blacklisted"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mail_contacts_account_id_is_blacklisted", table_name="mail_contacts")
    op.drop_column("mail_contacts", "is_blacklisted")
