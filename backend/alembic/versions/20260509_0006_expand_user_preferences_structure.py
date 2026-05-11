"""expand user preferences structure

Revision ID: 20260509_0006
Revises: 20260509_0005
Create Date: 2026-05-09 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260509_0006"
down_revision = "20260509_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mail_user_preferences",
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "mail_user_preferences",
        sa.Column("profile_title", sa.String(length=255), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "mail_user_preferences",
        sa.Column("avatar_url", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "mail_user_preferences",
        sa.Column("bio", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "mail_user_preferences",
        sa.Column("theme_mode", sa.String(length=20), nullable=False, server_default=sa.text("'light'")),
    )


def downgrade() -> None:
    op.drop_column("mail_user_preferences", "theme_mode")
    op.drop_column("mail_user_preferences", "bio")
    op.drop_column("mail_user_preferences", "avatar_url")
    op.drop_column("mail_user_preferences", "profile_title")
    op.drop_column("mail_user_preferences", "display_name")
