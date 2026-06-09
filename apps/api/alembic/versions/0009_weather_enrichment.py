"""Weather enrichment: add Diary lat/lon, relax Enrichment uniqueness to per-day.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29

This migration:
1. Adds nullable lat/lon columns to diaries (NUMERIC(9,6) — same precision as photos.lat/lon).
2. Drops the (entry_id, kind) unique constraint on enrichments.
3. Adds (entry_id, kind, captured_for_at) unique constraint to support per-day weather rows
   for multi-day entries.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diaries", sa.Column("lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("diaries", sa.Column("lon", sa.Numeric(9, 6), nullable=True))

    op.drop_constraint("uq_enrichments_entry_kind", "enrichments", type_="unique")
    op.create_unique_constraint(
        "uq_enrichments_entry_kind_captured",
        "enrichments",
        ["entry_id", "kind", "captured_for_at"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_enrichments_entry_kind_captured", "enrichments", type_="unique")
    op.create_unique_constraint(
        "uq_enrichments_entry_kind",
        "enrichments",
        ["entry_id", "kind"],
    )
    op.drop_column("diaries", "lon")
    op.drop_column("diaries", "lat")
