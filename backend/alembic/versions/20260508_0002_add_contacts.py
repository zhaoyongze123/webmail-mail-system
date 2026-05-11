"""add contacts tables

Revision ID: 20260508_0002
Revises: 20260508_0001
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260508_0002"
down_revision = "20260508_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mail_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("group_name", sa.String(length=100), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(length=50), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("account_id", "email", name=op.f("uq_mail_contacts_account_id_email")),
    )
    op.create_index(op.f("ix_mail_contacts_account_id"), "mail_contacts", ["account_id"], unique=False)
    op.create_index(
        "ix_mail_contacts_account_id_last_used_at",
        "mail_contacts",
        ["account_id", "last_used_at"],
        unique=False,
    )

    op.create_table(
        "mail_contact_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mail_contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("contact_id", "name", name=op.f("uq_mail_contact_tags_contact_id_name")),
    )
    op.create_index(op.f("ix_mail_contact_tags_contact_id"), "mail_contact_tags", ["contact_id"], unique=False)
    op.create_index(op.f("ix_mail_contact_tags_name"), "mail_contact_tags", ["name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_contact_tags_name"), table_name="mail_contact_tags")
    op.drop_index(op.f("ix_mail_contact_tags_contact_id"), table_name="mail_contact_tags")
    op.drop_table("mail_contact_tags")

    op.drop_index("ix_mail_contacts_account_id_last_used_at", table_name="mail_contacts")
    op.drop_index(op.f("ix_mail_contacts_account_id"), table_name="mail_contacts")
    op.drop_table("mail_contacts")
