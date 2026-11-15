"""Track display-copy attempts; index entry_photos by photo

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14

photos.display_attempted_at lets the display-copy backfill try photos it has
never tried before retrying ones that already failed, so permanently bad
photos cannot starve new ones. ix_entry_photos_photo_id serves lookups by
photo (the primary key leads with entry_id): the read-authorization check and
photo deletes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "photos", sa.Column("display_attempted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_entry_photos_photo_id", "entry_photos", ["photo_id"])


def downgrade() -> None:
    op.drop_index("ix_entry_photos_photo_id", table_name="entry_photos")
    op.drop_column("photos", "display_attempted_at")
