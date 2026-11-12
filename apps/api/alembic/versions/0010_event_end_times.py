"""Event end times, one-time rule evaluation, and body_source 'user'

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11

Entries are now created only for events that have ended (follow-up 25.1 in
POC_PHASE2_TODO.md). This migration:

1. Adds events.ends_at and events.rules_evaluated_at.
2. Lets entries.body_source be 'user' (a person edited the text).
3. Backfills occurred_at (all-day events had none) and ends_at from the stored
   Google payload, in the diary's timezone.
4. Marks every event that is attached or already over as evaluated, so shipping
   this does not re-run rules over the whole history.
5. One-off clean-up: untouched AI drafts whose events are all still in the
   future are moved to trash and their events released, to be evaluated when
   they end.

The downgrade does not restore the drafts trashed in step 5.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_BODY_SOURCES_BEFORE = "body_source IN ('llm','fallback','llm_polished','llm_hybrid')"
_BODY_SOURCES_AFTER = "body_source IN ('llm','fallback','llm_polished','llm_hybrid','user')"


def upgrade() -> None:
    op.add_column("events", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "events", sa.Column("rules_evaluated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_events_due_for_rules",
        "events",
        ["diary_id", "ends_at"],
        postgresql_where=sa.text("entry_id IS NULL AND rules_evaluated_at IS NULL"),
    )

    op.drop_constraint("ck_entries_body_source", "entries", type_="check")
    op.create_check_constraint("ck_entries_body_source", "entries", _BODY_SOURCES_AFTER)

    # All-day events: local midnight to local midnight in the diary's timezone.
    # Google's all-day end date is exclusive, so it is already the closing midnight.
    op.execute(
        """
        UPDATE events e
        SET occurred_at = ((e.payload->'start'->>'date')::date)::timestamp
                          AT TIME ZONE d.timezone,
            ends_at = COALESCE(
                          (e.payload->'end'->>'date')::date,
                          (e.payload->'start'->>'date')::date + 1
                      )::timestamp AT TIME ZONE d.timezone
        FROM diaries d
        WHERE d.id = e.diary_id
          AND e.source = 'google_calendar'
          AND e.payload->'start'->>'date' IS NOT NULL
        """
    )
    # Timed events: the stored end, or the start when Google sent no end.
    op.execute(
        """
        UPDATE events e
        SET ends_at = COALESCE((e.payload->'end'->>'dateTime')::timestamptz, e.occurred_at)
        WHERE e.source = 'google_calendar'
          AND e.payload->'start'->>'date' IS NULL
        """
    )

    op.execute(
        """
        UPDATE events
        SET rules_evaluated_at = now()
        WHERE entry_id IS NOT NULL OR ends_at IS NULL OR ends_at <= now()
        """
    )

    op.execute(
        """
        WITH doomed AS (
            SELECT en.id
            FROM entries en
            WHERE en.status = 'draft'
              AND en.body_source IN ('llm', 'fallback')
              AND en.creation_source = 'rule'
              AND en.deleted_at IS NULL
              AND EXISTS (SELECT 1 FROM events ev WHERE ev.entry_id = en.id)
              AND NOT EXISTS (
                  SELECT 1 FROM events ev
                  WHERE ev.entry_id = en.id AND (ev.ends_at IS NULL OR ev.ends_at <= now())
              )
        ),
        dropped_claims AS (
            DELETE FROM rule_series_claims WHERE entry_id IN (SELECT id FROM doomed)
        ),
        released AS (
            UPDATE events
            SET entry_id = NULL, rules_evaluated_at = NULL
            WHERE entry_id IN (SELECT id FROM doomed)
        )
        UPDATE entries SET deleted_at = now() WHERE id IN (SELECT id FROM doomed)
        """
    )


def downgrade() -> None:
    op.execute("UPDATE entries SET body_source = 'llm_hybrid' WHERE body_source = 'user'")
    op.drop_constraint("ck_entries_body_source", "entries", type_="check")
    op.create_check_constraint("ck_entries_body_source", "entries", _BODY_SOURCES_BEFORE)
    op.drop_index("ix_events_due_for_rules", table_name="events")
    op.drop_column("events", "rules_evaluated_at")
    op.drop_column("events", "ends_at")
