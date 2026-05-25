"""Decouple events from entries; add creation_source, auto_creation_rules, entry_rule_matches, rule_series_claims

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Make events.entry_id nullable
    op.alter_column("events", "entry_id", nullable=True)

    # 2. Add diary_id to events (needed to scope unattached events to a diary)
    op.add_column(
        "events",
        sa.Column(
            "diary_id",
            UUID(as_uuid=True),
            sa.ForeignKey("diaries.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # Back-fill diary_id for existing attached events from their entry's diary_id
    op.execute(
        """
        UPDATE events e
        SET diary_id = en.diary_id
        FROM entries en
        WHERE e.entry_id = en.id
          AND e.diary_id IS NULL
        """
    )
    op.create_index("ix_events_diary_id", "events", ["diary_id"])

    # 3. Add partial index for fast picker queries (unattached events by date)
    op.create_index(
        "ix_events_unattached_occurred",
        "events",
        ["occurred_at"],
        postgresql_where=sa.text("entry_id IS NULL"),
    )

    # 4. Add creation_source to entries
    op.add_column(
        "entries",
        sa.Column("creation_source", sa.String(20), nullable=True),
    )
    # Backfill existing rows
    op.execute(
        "UPDATE entries SET creation_source = 'legacy_auto' WHERE created_by = 'auto'"
    )
    op.execute(
        "UPDATE entries SET creation_source = 'manual' WHERE created_by = 'manual'"
    )
    op.alter_column("entries", "creation_source", nullable=False, server_default="manual")
    op.create_check_constraint(
        "ck_entries_creation_source",
        "entries",
        "creation_source IN ('manual','calendar_pick','rule','legacy_auto')",
    )

    # 5. New table: auto_creation_rules
    op.create_table(
        "auto_creation_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "diary_id",
            UUID(as_uuid=True),
            sa.ForeignKey("diaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("condition", JSONB, nullable=False),
        sa.Column("options", JSONB, nullable=False, server_default='{"recurring":"per_instance","multi_day":"spanning"}'),
        sa.Column("last_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_auto_creation_rules_diary_enabled",
        "auto_creation_rules",
        ["diary_id", "enabled"],
    )
    op.execute("""
        CREATE TRIGGER trg_auto_creation_rules_updated_at
        BEFORE UPDATE ON auto_creation_rules
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # 6. New table: entry_rule_matches
    op.create_table(
        "entry_rule_matches",
        sa.Column(
            "entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auto_creation_rules.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_entry_rule_matches_rule", "entry_rule_matches", ["rule_id"])

    # 7. New table: rule_series_claims
    op.create_table(
        "rule_series_claims",
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auto_creation_rules.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("recurring_event_id", sa.Text, nullable=False, primary_key=True),
        sa.Column(
            "entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_rule_series_claims_entry_id", "rule_series_claims", ["entry_id"])
    op.add_column(
        "scan_runs",
        sa.Column("rules_evaluated", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("rule_matches", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("scan_runs", "rule_matches")
    op.drop_column("scan_runs", "rules_evaluated")
    op.drop_index("ix_rule_series_claims_entry_id", table_name="rule_series_claims")
    op.drop_table("rule_series_claims")
    op.drop_index("ix_entry_rule_matches_rule", table_name="entry_rule_matches")
    op.drop_table("entry_rule_matches")
    op.drop_index("ix_auto_creation_rules_diary_enabled", table_name="auto_creation_rules")
    op.drop_table("auto_creation_rules")
    op.drop_constraint("ck_entries_creation_source", "entries", type_="check")
    op.drop_column("entries", "creation_source")
    op.drop_index("ix_events_unattached_occurred", table_name="events")
    op.drop_index("ix_events_diary_id", table_name="events")
    op.drop_column("events", "diary_id")
    # Remove any unattached events before restoring NOT NULL constraint
    op.execute("DELETE FROM events WHERE entry_id IS NULL")
    op.alter_column("events", "entry_id", nullable=False)
