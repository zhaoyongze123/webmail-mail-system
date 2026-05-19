"""add local mailbox password hash

Revision ID: 20260519_0008
Revises: 20260518_0007
Create Date: 2026-05-19 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0008"
down_revision = "20260518_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mail_accounts", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("mail_accounts", "password_hash")
