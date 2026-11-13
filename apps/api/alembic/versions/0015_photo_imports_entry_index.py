"""Index photo imports by entry and user

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-13

An entry page lists the caller's imports into that entry
(WHERE entry_id = ? AND user_id = ?), and deleting an entry cascades to its
imports by entry_id: both use this index.
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_photo_imports_entry_user", "photo_imports", ["entry_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_photo_imports_entry_user", table_name="photo_imports")
