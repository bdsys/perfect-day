"""Rename tiers to free/plus/family; add ai_draft_spends

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-15

tier1 becomes plus and tier2 becomes family; the check constraint follows.
ai_draft_spends records one row per AI draft a user has spent in a calendar
month. The unique key makes a retried spend idempotent, and the row outlives
its entry (entry_id is set null) so deleting an entry never refunds a draft.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_users_subscription_tier", "users", type_="check")
    op.execute(
        "UPDATE users SET subscription_tier = CASE subscription_tier "
        "WHEN 'tier1' THEN 'plus' WHEN 'tier2' THEN 'family' "
        "ELSE subscription_tier END"
    )
    op.create_check_constraint(
        "ck_users_subscription_tier",
        "users",
        "subscription_tier IN ('free','plus','family')",
    )
    op.create_table(
        "ai_draft_spends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("key", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_ai_draft_spends_user_month", "ai_draft_spends", ["user_id", "month"])


def downgrade() -> None:
    op.drop_index("ix_ai_draft_spends_user_month", table_name="ai_draft_spends")
    op.drop_table("ai_draft_spends")
    op.drop_constraint("ck_users_subscription_tier", "users", type_="check")
    op.execute(
        "UPDATE users SET subscription_tier = CASE subscription_tier "
        "WHEN 'plus' THEN 'tier1' WHEN 'family' THEN 'tier2' "
        "ELSE subscription_tier END"
    )
    op.create_check_constraint(
        "ck_users_subscription_tier",
        "users",
        "subscription_tier IN ('free','tier1','tier2')",
    )
