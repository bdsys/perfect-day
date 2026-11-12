"""Entries are created only for events that have ended (follow-up 25.1).

Covers ingest, the per-scan sweep for events that have ended since they were
stored, cancellations and reschedules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Diary, Entry, Event
from tests.fixtures.factories import make_diary, make_event, make_user

SOCCER_RULE = {
    "op": "AND",
    "children": [
        {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": False}
    ],
}
PER_INSTANCE = {"recurring": "per_instance", "multi_day": "per_day"}


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _timed_event(external_id: str, start: datetime, end: datetime, **extra) -> dict:
    return {
        "id": external_id,
        "summary": "Soccer practice",
        "status": "confirmed",
        "start": {"dateTime": _iso(start)},
        "end": {"dateTime": _iso(end)},
        **extra,
    }


async def _diary_with_rule(db: AsyncSession, timezone: str = "UTC") -> Diary:
    user = await make_user(db)
    diary = await make_diary(db, owner=user, timezone=timezone)
    db.add(
        AutoCreationRule(
            diary_id=diary.id, name="Soccer", enabled=True, condition=SOCCER_RULE,
            options=PER_INSTANCE,
        )
    )
    await db.commit()
    return diary


async def _event(db: AsyncSession, event_id: str) -> Event:
    return (
        await db.execute(
            select(Event).where(Event.id == uuid.UUID(event_id)).execution_options(
                populate_existing=True
            )
        )
    ).scalar_one()


class TestIngest:
    async def test_future_event_is_stored_but_not_evaluated(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _ingest_calendar_event

        diary = await _diary_with_rule(db_session)
        start = datetime.now(tz=UTC).replace(microsecond=0) + timedelta(days=3)
        end = start + timedelta(hours=1)

        with patch("app.workers.tasks.evaluate_rules_for_event") as evaluate:
            event_id = await _ingest_calendar_event(
                _timed_event("future-1", start, end), diary.id, "UTC"
            )

        event = await _event(db_session, event_id)
        assert event.occurred_at == start
        assert event.ends_at == end
        assert event.rules_evaluated_at is None
        evaluate.delay.assert_not_called()

    async def test_ended_event_is_evaluated_straight_away(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _ingest_calendar_event

        diary = await _diary_with_rule(db_session)
        start = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(days=1)

        with patch("app.workers.tasks.evaluate_rules_for_event") as evaluate:
            event_id = await _ingest_calendar_event(
                _timed_event("past-1", start, start + timedelta(hours=1)), diary.id, "UTC"
            )

        evaluate.delay.assert_called_once_with(event_id, str(diary.id))

    async def test_all_day_event_gets_a_stored_time_in_the_diary_timezone(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _ingest_calendar_event

        diary = await _diary_with_rule(db_session, timezone="America/Chicago")
        all_day = {
            "id": "all-day-1",
            "summary": "Soccer tournament",
            "start": {"date": "2026-06-01"},
            "end": {"date": "2026-06-02"},
        }

        with patch("app.workers.tasks.evaluate_rules_for_event"):
            event_id = await _ingest_calendar_event(all_day, diary.id, "America/Chicago")

        event = await _event(db_session, event_id)
        assert event.occurred_at == datetime(2026, 6, 1, 5, 0, tzinfo=UTC)
        assert event.ends_at == datetime(2026, 6, 2, 5, 0, tzinfo=UTC)


class TestDueSweep:
    async def test_queues_only_unattached_unevaluated_events_that_have_ended(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import queue_due_rule_evaluations

        diary = await _diary_with_rule(db_session)
        now = datetime.now(tz=UTC)
        due = await make_event(db_session, diary_id=diary.id, ends_at=now - timedelta(minutes=5))
        await make_event(db_session, diary_id=diary.id, ends_at=now + timedelta(days=2))
        await make_event(
            db_session,
            diary_id=diary.id,
            ends_at=now - timedelta(days=2),
            rules_evaluated_at=now - timedelta(days=1),
        )
        other_diary = await make_diary(
            db_session, owner=await make_user(db_session), name="Other diary"
        )
        await make_event(db_session, diary_id=other_diary.id, ends_at=now - timedelta(minutes=5))

        with patch("app.workers.tasks.evaluate_rules_for_event") as evaluate:
            queued = await queue_due_rule_evaluations(diary.id)

        assert queued == 1
        evaluate.delay.assert_called_once_with(str(due.id), str(diary.id))

    async def test_scan_evaluates_an_event_that_ended_since_it_was_stored(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _scan_diary

        diary = await _diary_with_rule(db_session)
        due = await make_event(
            db_session, diary_id=diary.id, ends_at=datetime.now(tz=UTC) - timedelta(minutes=5)
        )

        # No Google connection: the sweep must still run.
        with patch("app.workers.tasks.evaluate_rules_for_event") as evaluate:
            await _scan_diary(str(diary.id))

        evaluate.delay.assert_called_once_with(str(due.id), str(diary.id))

    async def test_apply_to_past_days_creates_nothing_for_a_future_event(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _apply_rule_backfill

        diary = await _diary_with_rule(db_session)
        rule_id = (await db_session.execute(select(AutoCreationRule.id))).scalar_one()
        now = datetime.now(tz=UTC)
        await make_event(
            db_session,
            diary_id=diary.id,
            payload={"summary": "Soccer practice", "start": {}, "end": {}},
            occurred_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=1),
        )

        with patch("app.workers.tasks.generate_entry_draft"):
            await _apply_rule_backfill(str(rule_id), 30)

        assert (await db_session.execute(select(Entry.id))).scalars().all() == []


async def _attached(
    db: AsyncSession,
    diary: Diary,
    *,
    external_id: str = "evt-1",
    body_source: str = "llm",
    status: str = "draft",
    creation_source: str = "rule",
    ends_at: datetime | None = None,
) -> tuple[Entry, Event]:
    """An entry with one calendar event on it."""
    entry = Entry(
        diary_id=diary.id,
        entry_date=datetime(2026, 6, 1, tzinfo=UTC).date(),
        title="Soccer practice",
        body_markdown="Practice ran from ten until eleven.",
        body_source=body_source,
        status=status,
        created_by="auto",
        creation_source=creation_source,
    )
    db.add(entry)
    await db.commit()
    event = await make_event(
        db,
        diary_id=diary.id,
        entry=entry,
        external_id=external_id,
        ends_at=ends_at or datetime.now(tz=UTC) - timedelta(days=1),
        rules_evaluated_at=datetime.now(tz=UTC) - timedelta(hours=1),
    )
    return entry, event


async def _entry_row(db: AsyncSession, entry_id: uuid.UUID):
    return (
        await db.execute(
            select(Entry.deleted_at, Entry.body_markdown, Entry.status).where(Entry.id == entry_id)
        )
    ).one()


async def _external_ids(db: AsyncSession) -> list[str]:
    return sorted((await db.execute(select(Event.external_id))).scalars().all())


class TestCancellation:
    async def test_cancelled_before_any_entry_leaves_no_event(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        await make_event(db_session, diary_id=diary.id, external_id="evt-1")

        await _cancel_calendar_event("evt-1", str(diary.id))

        assert await _external_ids(db_session) == []

    async def test_cancelled_after_an_untouched_draft_removes_the_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.models import RuleSeriesClaim
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        entry, _ = await _attached(db_session, diary)
        rule_id = (await db_session.execute(select(AutoCreationRule.id))).scalar_one()
        db_session.add(
            RuleSeriesClaim(rule_id=rule_id, recurring_event_id="series-1", entry_id=entry.id)
        )
        await db_session.commit()

        with patch("app.workers.tasks.generate_entry_draft") as draft:
            await _cancel_calendar_event("evt-1", str(diary.id))

        assert (await _entry_row(db_session, entry.id)).deleted_at is not None
        assert await _external_ids(db_session) == []
        claims = (await db_session.execute(select(RuleSeriesClaim.entry_id))).scalars().all()
        assert claims == [], "a later instance must not be attached to the trashed entry"
        draft.delay.assert_not_called()

    async def test_cancelled_after_an_edited_draft_leaves_the_text_intact(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        entry, _ = await _attached(db_session, diary, body_source="user")

        with patch("app.workers.tasks.generate_entry_draft") as draft:
            await _cancel_calendar_event("evt-1", str(diary.id))

        row = await _entry_row(db_session, entry.id)
        assert row.deleted_at is None
        assert row.body_markdown == "Practice ran from ten until eleven."
        assert await _external_ids(db_session) == []
        draft.delay.assert_not_called()

    async def test_cancelled_after_publishing_leaves_the_entry_published(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        entry, _ = await _attached(db_session, diary, status="published")

        with patch("app.workers.tasks.generate_entry_draft") as draft:
            await _cancel_calendar_event("evt-1", str(diary.id))

        row = await _entry_row(db_session, entry.id)
        assert (row.deleted_at, row.status) == (None, "published")
        assert row.body_markdown == "Practice ran from ten until eleven."
        draft.delay.assert_not_called()

    async def test_entry_a_person_made_from_the_picker_is_never_removed(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        entry, _ = await _attached(db_session, diary, creation_source="calendar_pick")

        with patch("app.workers.tasks.generate_entry_draft"):
            await _cancel_calendar_event("evt-1", str(diary.id))

        assert (await _entry_row(db_session, entry.id)).deleted_at is None
        assert await _external_ids(db_session) == []

    async def test_untouched_draft_with_other_events_is_kept_and_redrafted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.models import RuleSeriesClaim
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        entry, _ = await _attached(db_session, diary)
        await make_event(db_session, diary_id=diary.id, entry=entry, external_id="evt-2")
        rule_id = (await db_session.execute(select(AutoCreationRule.id))).scalar_one()
        db_session.add(
            RuleSeriesClaim(rule_id=rule_id, recurring_event_id="series-1", entry_id=entry.id)
        )
        await db_session.commit()

        with patch("app.workers.tasks.generate_entry_draft") as draft:
            await _cancel_calendar_event("evt-1", str(diary.id))

        assert (await _entry_row(db_session, entry.id)).deleted_at is None
        assert await _external_ids(db_session) == ["evt-2"]
        claims = (await db_session.execute(select(RuleSeriesClaim.entry_id))).scalars().all()
        assert claims == [entry.id], "the series is still running"
        draft.delay.assert_called_once_with(str(entry.id))

    async def test_cancelling_an_event_we_never_stored_does_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        await make_event(db_session, diary_id=diary.id, external_id="evt-1")

        await _cancel_calendar_event("never-seen", str(diary.id))

        assert await _external_ids(db_session) == ["evt-1"]

    async def test_cancellation_in_one_diary_does_not_touch_another(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event

        diary = await _diary_with_rule(db_session)
        other = await make_diary(db_session, owner=await make_user(db_session), name="Other")
        await make_event(db_session, diary_id=other.id, external_id="evt-1")

        await _cancel_calendar_event("evt-1", str(diary.id))

        assert await _external_ids(db_session) == ["evt-1"]
