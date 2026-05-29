"""Add body_source column to entries

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column as nullable first so the backfill can run before enforcing NOT NULL
    op.add_column(
        "entries",
        sa.Column("body_source", sa.String(20), nullable=True),
    )

    # Backfill all existing rows — they were all LLM-generated (or blank)
    op.execute("UPDATE entries SET body_source = 'llm' WHERE body_source IS NULL")

    # Now tighten to NOT NULL with a default for future inserts
    op.alter_column("entries", "body_source", nullable=False, server_default="llm")

    op.create_check_constraint(
        "ck_entries_body_source",
        "entries",
        "body_source IN ('llm','fallback')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_entries_body_source", "entries", type_="check")
    op.drop_column("entries", "body_source")
