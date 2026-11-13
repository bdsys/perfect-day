"""Per-entry place and time zone; a diary's home place name

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-12

PoC item 24. An entry gets its own place (name and coordinates) and where it
came from: the parent, a photo's GPS, a calendar event's location text, or
the diary's home place. An optional per-entry time zone moves the entry's
event times and date. Diaries already have lat/lon; they gain a place name.
Spec: docs/superpowers/specs/2026-11-12-per-entry-location-design.md
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entries", sa.Column("location_name", sa.Text(), nullable=True))
    op.add_column("entries", sa.Column("lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("entries", sa.Column("lon", sa.Numeric(9, 6), nullable=True))
    op.add_column("entries", sa.Column("location_source", sa.String(10), nullable=True))
    op.add_column("entries", sa.Column("tz_override", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_entries_location_source",
        "entries",
        "location_source IS NULL OR location_source IN ('parent','photo','event','home')",
    )
    op.add_column("diaries", sa.Column("location_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("diaries", "location_name")
    op.drop_constraint("ck_entries_location_source", "entries", type_="check")
    for column in ("tz_override", "location_source", "lon", "lat", "location_name"):
        op.drop_column("entries", column)
