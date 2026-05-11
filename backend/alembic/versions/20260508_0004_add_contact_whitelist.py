"""add contact whitelist flag

Revision ID: 20260508_0004
Revises: 20260508_0003
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260508_0004"
down_revision = "20260508_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_contacts",
        sa.Column("is_whitelisted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("mail_contacts", "is_whitelisted")
