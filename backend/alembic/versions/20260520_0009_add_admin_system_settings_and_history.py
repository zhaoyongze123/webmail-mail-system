"""add admin system settings and action history

Revision ID: 20260520_0009
Revises: 20260519_0008
Create Date: 2026-05-20 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260520_0009"
down_revision = "20260519_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_system_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("theme", sa.String(length=20), nullable=False, server_default=sa.text("'system'")),
        sa.Column("language", sa.String(length=20), nullable=False, server_default=sa.text("'zh-CN'")),
        sa.Column("queue_auto_refresh_seconds", sa.Integer(), nullable=False, server_default=sa.text("15")),
        sa.Column("queue_max_items", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("audit_default_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("log_retention_days", sa.Integer(), nullable=False, server_default=sa.text("14")),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "admin_action_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], name=op.f("fk_admin_action_history_admin_user_id_admin_users"), ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_admin_action_history_admin_user_id"), "admin_action_history", ["admin_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_action_history_admin_user_id"), table_name="admin_action_history")
    op.drop_table("admin_action_history")
    op.drop_table("admin_system_settings")
