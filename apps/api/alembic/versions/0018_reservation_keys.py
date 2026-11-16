"""Reservation keys on llm_generations; sweepable ai_draft_spends

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-16

llm_generations.reservation_key ties a successful generation to the
ai_draft_spends row it spent, so the sweeper can tell a lost draft (worker
killed, no success) from a finished one. ai_draft_spends.sweepable defaults
false so spends made before this migration are never refunded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_generations", sa.Column("reservation_key", sa.Text, nullable=True))
    op.create_index(
        "ix_llm_generations_reservation_key", "llm_generations", ["reservation_key"]
    )
    op.add_column(
        "ai_draft_spends",
        sa.Column("sweepable", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ai_draft_spends", "sweepable")
    op.drop_index("ix_llm_generations_reservation_key", table_name="llm_generations")
    op.drop_column("llm_generations", "reservation_key")
