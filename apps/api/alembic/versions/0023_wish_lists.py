"""wish_lists, wish_list_items, wish_list_claims

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "wish_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "diary_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("diaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=False),
        sa.Column("pasted_list_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_ts(),
        sa.CheckConstraint("status IN ('draft','published')", name="ck_wish_lists_status"),
    )
    op.create_index("ix_wish_lists_diary_id", "wish_lists", ["diary_id"])

    op.create_table(
        "wish_list_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "wish_list_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wish_lists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("many", sa.Boolean(), nullable=False, server_default="false"),
        *_ts(),
    )
    op.create_index("ix_wish_list_items_wish_list_id", "wish_list_items", ["wish_list_id"])

    op.create_table(
        "wish_list_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wish_list_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(60), nullable=True),
        sa.Column("secret_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR name IS NOT NULL", name="ck_wish_list_claims_who"
        ),
    )
    op.create_index("ix_wish_list_claims_item_id", "wish_list_claims", ["item_id"])
    op.create_index(
        "uq_wish_list_claims_item_user",
        "wish_list_claims",
        ["item_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("wish_list_claims")
    op.drop_table("wish_list_items")
    op.drop_table("wish_lists")
