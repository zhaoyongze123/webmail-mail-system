"""add admin backend foundation

Revision ID: 20260518_0007
Revises: 20260509_0006
Create Date: 2026-05-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260518_0007"
down_revision = "20260509_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("quota_limit_mb", sa.Integer(), nullable=False, server_default=sa.text("10240")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name=op.f("uq_mail_domains_name")),
    )

    op.add_column(
        "mail_accounts",
        sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "mail_accounts",
        sa.Column("quota_mb", sa.Integer(), nullable=False, server_default=sa.text("500")),
    )
    op.add_column(
        "mail_accounts",
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
    )
    op.add_column(
        "mail_accounts",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "mail_accounts",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_mail_accounts_domain_id"), "mail_accounts", ["domain_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_mail_accounts_domain_id_mail_domains"),
        "mail_accounts",
        "mail_domains",
        ["domain_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "mail_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_address", sa.String(length=320), nullable=False),
        sa.Column("target_addresses", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["domain_id"], ["mail_domains.id"], name=op.f("fk_mail_aliases_domain_id_mail_domains"), ondelete="CASCADE"),
        sa.UniqueConstraint("source_address", name=op.f("uq_mail_aliases_source_address")),
    )
    op.create_index(op.f("ix_mail_aliases_domain_id"), "mail_aliases", ["domain_id"], unique=False)

    op.create_table(
        "quota_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("default_quota_mb", sa.Integer(), nullable=False, server_default=sa.text("500")),
        sa.Column("warn_80_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("warn_90_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("warn_95_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["domain_id"], ["mail_domains.id"], name=op.f("fk_quota_policies_domain_id_mail_domains"), ondelete="CASCADE"),
        sa.UniqueConstraint("domain_id", name=op.f("uq_quota_policies_domain_id")),
    )
    op.create_index(op.f("ix_quota_policies_domain_id"), "quota_policies", ["domain_id"], unique=False)

    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=sa.text("'superadmin'")),
        sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("totp_secret", sa.String(length=255), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["domain_id"], ["mail_domains.id"], name=op.f("fk_admin_users_domain_id_mail_domains"), ondelete="SET NULL"),
        sa.UniqueConstraint("username", name=op.f("uq_admin_users_username")),
    )
    op.create_index(op.f("ix_admin_users_domain_id"), "admin_users", ["domain_id"], unique=False)

    op.create_table(
        "admin_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], name=op.f("fk_admin_refresh_tokens_admin_user_id_admin_users"), ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_admin_refresh_tokens_admin_user_id"), "admin_refresh_tokens", ["admin_user_id"], unique=False)

    op.add_column(
        "audit_logs",
        sa.Column("actor_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("actor_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("target_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("target_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "target_id")
    op.drop_column("audit_logs", "target_type")
    op.drop_column("audit_logs", "actor_id")
    op.drop_column("audit_logs", "actor_type")

    op.drop_index(op.f("ix_admin_refresh_tokens_admin_user_id"), table_name="admin_refresh_tokens")
    op.drop_table("admin_refresh_tokens")

    op.drop_index(op.f("ix_admin_users_domain_id"), table_name="admin_users")
    op.drop_table("admin_users")

    op.drop_index(op.f("ix_quota_policies_domain_id"), table_name="quota_policies")
    op.drop_table("quota_policies")

    op.drop_index(op.f("ix_mail_aliases_domain_id"), table_name="mail_aliases")
    op.drop_table("mail_aliases")

    op.drop_constraint(op.f("fk_mail_accounts_domain_id_mail_domains"), "mail_accounts", type_="foreignkey")
    op.drop_index(op.f("ix_mail_accounts_domain_id"), table_name="mail_accounts")
    op.drop_column("mail_accounts", "last_login_at")
    op.drop_column("mail_accounts", "is_admin")
    op.drop_column("mail_accounts", "status")
    op.drop_column("mail_accounts", "quota_mb")
    op.drop_column("mail_accounts", "domain_id")

    op.drop_table("mail_domains")
