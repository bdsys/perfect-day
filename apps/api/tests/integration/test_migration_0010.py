"""Migration 0010 against real rows: time backfill, evaluated marks, one-off clean-up.

Runs in its own database on the test Postgres container, so stepping the schema
back to 0009 cannot disturb the other tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

SCRATCH_DB = "migration_0010_test"


@pytest.fixture
def scratch_db_url(db_url_sync):
    admin = sa.create_engine(db_url_sync, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        conn.execute(sa.text(f"CREATE DATABASE {SCRATCH_DB}"))
    yield db_url_sync.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
    admin.dispose()


def _alembic(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _entry(conn, diary_id, *, status="draft", body_source="llm", creation_source="rule"):
    entry_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO entries (id, diary_id, entry_date, body_markdown, body_source, status,"
            " created_by, creation_source)"
            " VALUES (:id, :d, '2026-01-01', 'Draft text.', :bs, :st, 'auto', :cs)"
        ),
        {"id": entry_id, "d": diary_id, "bs": body_source, "st": status, "cs": creation_source},
    )
    return entry_id


def _event(conn, diary_id, payload: str, *, entry_id=None, occurred_at=None):
    event_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO events (id, diary_id, entry_id, source, external_id, occurred_at, payload)"
            " VALUES (:id, :d, :e, 'google_calendar', :x, :o, CAST(:p AS jsonb))"
        ),
        {"id": event_id, "d": diary_id, "e": entry_id, "x": event_id.hex, "o": occurred_at,
         "p": payload},
    )
    return event_id


def test_upgrade_backfills_times_and_trashes_untouched_drafts_for_future_events(scratch_db_url):
    command.upgrade(_alembic(scratch_db_url), "0009")
    engine = sa.create_engine(scratch_db_url)
    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).date()
    day_after = tomorrow + timedelta(days=1)
    all_day_future = f'{{"start": {{"date": "{tomorrow}"}}, "end": {{"date": "{day_after}"}}}}'
    timed_past = (
        '{"start": {"dateTime": "2026-01-10T10:00:00-06:00"},'
        ' "end": {"dateTime": "2026-01-10T11:30:00-06:00"}}'
    )
    all_day_past = '{"start": {"date": "2026-01-05"}, "end": {"date": "2026-01-07"}}'

    with engine.begin() as conn:
        user_id, diary_id, rule_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        conn.execute(
            sa.text("INSERT INTO users (id, email) VALUES (:id, 'm@example.com')"), {"id": user_id}
        )
        conn.execute(
            sa.text(
                "INSERT INTO diaries (id, owner_user_id, name, slug, timezone)"
                " VALUES (:id, :u, 'D', 'd', 'America/Chicago')"
            ),
            {"id": diary_id, "u": user_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO auto_creation_rules (id, diary_id, name, enabled, condition, options)"
                " VALUES (:id, :d, 'R', true, '{}'::jsonb, '{}'::jsonb)"
            ),
            {"id": rule_id, "d": diary_id},
        )
        untouched_future = _entry(conn, diary_id)
        published_future = _entry(conn, diary_id, status="published")
        untouched_past = _entry(conn, diary_id)
        conn.execute(
            sa.text(
                "INSERT INTO rule_series_claims (rule_id, recurring_event_id, entry_id)"
                " VALUES (:r, 'series-1', :e)"
            ),
            {"r": rule_id, "e": untouched_future},
        )
        ev_future_untouched = _event(conn, diary_id, all_day_future, entry_id=untouched_future)
        ev_future_published = _event(conn, diary_id, all_day_future, entry_id=published_future)
        ev_future_loose = _event(conn, diary_id, all_day_future)
        ev_past_timed = _event(
            conn, diary_id, timed_past, entry_id=untouched_past,
            occurred_at=datetime(2026, 1, 10, 16, 0, tzinfo=UTC),
        )
        ev_past_all_day = _event(conn, diary_id, all_day_past)

    command.upgrade(_alembic(scratch_db_url), "head")

    with engine.connect() as conn:
        events = {
            row.id: row
            for row in conn.execute(
                sa.text("SELECT id, entry_id, occurred_at, ends_at, rules_evaluated_at FROM events")
            )
        }
        deleted = {
            row.id: row.deleted_at
            for row in conn.execute(sa.text("SELECT id, deleted_at FROM entries"))
        }
        claims = conn.execute(sa.text("SELECT count(*) FROM rule_series_claims")).scalar_one()
    engine.dispose()

    # Times: Chicago is UTC-6 in January, so local midnight is 06:00 UTC.
    assert events[ev_past_all_day].occurred_at == datetime(2026, 1, 5, 6, 0, tzinfo=UTC)
    assert events[ev_past_all_day].ends_at == datetime(2026, 1, 7, 6, 0, tzinfo=UTC)
    assert events[ev_past_timed].occurred_at == datetime(2026, 1, 10, 16, 0, tzinfo=UTC)
    assert events[ev_past_timed].ends_at == datetime(2026, 1, 10, 17, 30, tzinfo=UTC)

    # Already over, or attached to something we keep: never evaluated again.
    assert events[ev_past_all_day].rules_evaluated_at is not None
    assert events[ev_past_timed].rules_evaluated_at is not None
    assert events[ev_future_published].rules_evaluated_at is not None
    # Still to come and unattached: evaluated when it ends.
    assert events[ev_future_loose].rules_evaluated_at is None

    # The untouched draft for a future event goes to trash and releases its event.
    assert deleted[untouched_future] is not None
    assert events[ev_future_untouched].entry_id is None
    assert events[ev_future_untouched].rules_evaluated_at is None
    assert claims == 0
    # A published entry and a draft for a past event are left alone.
    assert deleted[published_future] is None
    assert events[ev_future_published].entry_id == published_future
    assert deleted[untouched_past] is None
    assert events[ev_past_timed].entry_id == untouched_past


def test_downgrade_then_upgrade_round_trips(scratch_db_url):
    cfg = _alembic(scratch_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0009")
    command.upgrade(cfg, "head")

    engine = sa.create_engine(scratch_db_url)
    with engine.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'events'"
                )
            )
        }
    engine.dispose()
    assert {"ends_at", "rules_evaluated_at"} <= columns
