"""Pending AI suggestions on entries

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-12

When an entry already has text, the AI's new version is stored beside it as a
suggestion instead of overwriting it (follow-up 25.3 in POC_PHASE2_TODO.md).
One suggestion per entry; a new one replaces the old.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_COLUMNS = (
    "suggested_title",
    "suggested_body_markdown",
    "suggested_flagged_tokens",
    "suggested_body_source",
    "suggested_at",
)


def upgrade() -> None:
    op.add_column("entries", sa.Column("suggested_title", sa.Text(), nullable=True))
    op.add_column("entries", sa.Column("suggested_body_markdown", sa.Text(), nullable=True))
    op.add_column(
        "entries", sa.Column("suggested_flagged_tokens", ARRAY(sa.Text()), nullable=True)
    )
    op.add_column("entries", sa.Column("suggested_body_source", sa.String(20), nullable=True))
    op.add_column(
        "entries", sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_entries_suggested_body_source",
        "entries",
        "suggested_body_source IS NULL"
        " OR suggested_body_source IN ('llm','llm_polished','llm_hybrid')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_entries_suggested_body_source", "entries", type_="check")
    for column in reversed(_COLUMNS):
        op.drop_column("entries", column)
