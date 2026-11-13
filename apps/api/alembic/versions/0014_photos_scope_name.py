"""Drop the retired Google Photos Library scope name from stored grants

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-13

Google removed photoslibrary.readonly in March 2025; photos now come through
the Picker API (photospicker.mediaitems.readonly). Data-only.
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

REMOVE_OLD_SCOPE_SQL = (
    "UPDATE oauth_tokens SET scopes_granted = array_remove(scopes_granted, "
    "'photoslibrary.readonly') WHERE 'photoslibrary.readonly' = ANY(scopes_granted)"
)


def upgrade() -> None:
    op.execute(REMOVE_OLD_SCOPE_SQL)


def downgrade() -> None:
    # No-op: the removed scope name no longer exists at Google, so there is
    # nothing meaningful to restore.
    pass
