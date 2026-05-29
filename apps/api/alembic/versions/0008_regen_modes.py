"""Regen modes: expand body_source on entries + add mode to llm_generations

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-28

This migration:
1. Expands the body_source constraint on entries to include 'llm_polished' and 'llm_hybrid'
2. Adds a nullable mode column to llm_generations
3. Backfills all existing llm_generation rows with mode='events'
4. Constrains mode to NOT NULL and adds CHECK constraint
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop and recreate the body_source constraint on entries
    op.drop_constraint("ck_entries_body_source", "entries", type_="check")
    op.create_check_constraint(
        "ck_entries_body_source",
        "entries",
        "body_source IN ('llm','fallback','llm_polished','llm_hybrid')",
    )

    # 2. Add mode column as nullable first
    op.add_column(
        "llm_generations",
        sa.Column("mode", sa.String(10), nullable=True),
    )

    # 3. Backfill all existing rows — they were all event-based generations
    op.execute("UPDATE llm_generations SET mode = 'events' WHERE mode IS NULL")

    # 4. Alter the column to NOT NULL
    op.alter_column("llm_generations", "mode", nullable=False)

    # 5. Add CHECK constraint on mode
    op.create_check_constraint(
        "ck_llm_generations_mode",
        "llm_generations",
        "mode IN ('events','polish','hybrid','none')",
    )


def downgrade() -> None:
    # Reverse the constraint and column changes in reverse order
    op.drop_constraint("ck_llm_generations_mode", "llm_generations", type_="check")
    op.drop_column("llm_generations", "mode")
    op.drop_constraint("ck_entries_body_source", "entries", type_="check")
    op.create_check_constraint(
        "ck_entries_body_source",
        "entries",
        "body_source IN ('llm','fallback')",
    )
