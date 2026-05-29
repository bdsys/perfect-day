"""Drop legacy_auto entries/events; change event uniqueness to per-diary

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-25

Before migration 0005 every calendar event was auto-attached to an Entry
during scan. Migration 0005 decoupled events from entries but left all
existing events still pointing to their auto-created entries, making the
calendar picker show zero results.

This migration:
1. Deletes all events and entries from the pre-0005 auto-attach era
   (creation_source='legacy_auto').
2. Removes 'legacy_auto' from the entries check constraint — it no longer
   has any meaning in the current code.
3. Rescopes the events uniqueness from global (source, external_id) to
   per-diary (diary_id, source, external_id) so the same Google Calendar
   event can appear in multiple diaries.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Delete legacy events first (FK events.entry_id → entries.id).
    #    Entries with creation_source='legacy_auto' only exist because of the
    #    old auto-attach behaviour; they have no user-created content.
    op.execute(
        """
        DELETE FROM events
        WHERE entry_id IN (
            SELECT id FROM entries WHERE creation_source = 'legacy_auto'
        )
        """
    )
    op.execute("DELETE FROM entries WHERE creation_source = 'legacy_auto'")

    # 2. Swap the check constraint: drop 'legacy_auto' from the allowed values.
    op.drop_constraint("ck_entries_creation_source", "entries", type_="check")
    op.create_check_constraint(
        "ck_entries_creation_source",
        "entries",
        "creation_source IN ('manual','calendar_pick','rule')",
    )

    # 3. Rescope event uniqueness from global to per-diary.
    op.drop_index("ix_events_source_external_id", table_name="events")
    op.create_index(
        "ix_events_diary_source_external_id",
        "events",
        ["diary_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Restore global uniqueness index.
    op.drop_index("ix_events_diary_source_external_id", table_name="events")
    op.create_index(
        "ix_events_source_external_id",
        "events",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # Restore check constraint with legacy_auto (needed for downgrade
    # compatibility even though no rows use that value after this migration).
    op.drop_constraint("ck_entries_creation_source", "entries", type_="check")
    op.create_check_constraint(
        "ck_entries_creation_source",
        "entries",
        "creation_source IN ('manual','calendar_pick','rule','legacy_auto')",
    )
    # Deleted rows are NOT restored.
