"""An edit to a calendar event redrafts the draft it belongs to (row 33).

The redraft goes through queue_redraft, so it is free and coalesced; whether it
rewrites in place or becomes a suggestion is the drafting code's call, pinned in
tests/integration/test_suggestions.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.models import Diary, Entry, Event, User
from tests.fixtures.factories import make_diary, make_event, make_user

STORED = {
    "summary": "Soccer practice",
    "location": "Field 3",
    "description": "Bring water",
    "start": {},
    "end": {},
    "status": "confirmed",
    "recurringEventId": None,
    "attendees": [
        {
            "displayName": "Grandma",
            "email": "g@example.com",
            "organizer": False,
            "responseStatus": "needsAction",
        }
    ],
}
ENDED_START = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _google(external_id: str = "evt-1", start: datetime = ENDED_START, **changes) -> dict:
    """The event as Google sends it again: same id, same past time unless moved."""
    data = {
        "id": external_id,
        "summary": STORED["summary"],
        "location": STORED["location"],
        "description": STORED["description"],
        "status": "confirmed",
        "start": {"dateTime": _iso(start)},
        "end": {"dateTime": _iso(start + timedelta(hours=1))},
        "attendees": [dict(a) for a in STORED["attendees"]],
    }
    data.update(changes)
    return data


async def _diary(db: AsyncSession) -> Diary:
    user = await make_user(db)
    return await make_diary(db, owner=user, timezone="UTC")


async def _entry_with_event(
    db: AsyncSession,
    diary: Diary,
    *,
    status: str = "draft",
    body_source: str = "llm",
    deleted: bool = False,
    external_ids: tuple[str, ...] = ("evt-1",),
) -> Entry:
    entry = Entry(
        diary_id=diary.id,
        entry_date=ENDED_START.date(),
        title="Soccer practice",
        body_markdown="Practice ran from ten until eleven.",
        body_source=body_source,
        status=status,
        created_by="auto",
        creation_source="rule",
        deleted_at=datetime.now(tz=UTC) if deleted else None,
    )
    db.add(entry)
    await db.commit()
    for external_id in external_ids:
        await make_event(
            db,
            diary_id=diary.id,
            entry=entry,
            external_id=external_id,
            payload=dict(STORED),
            occurred_at=ENDED_START,
            ends_at=ENDED_START + timedelta(hours=1),
            rules_evaluated_at=datetime.now(tz=UTC) - timedelta(hours=1),
        )
    return entry


async def _ingest(diary: Diary, data: dict, redraft: AsyncMock) -> tuple[str | None, list[dict]]:
    from app.workers.tasks import _ingest_calendar_event

    with (
        patch("app.workers.tasks.evaluate_rules_for_event"),
        patch("app.workers.tasks.queue_redraft", redraft),
        capture_logs() as logs,
        # The app caches loggers on first use (app/core/logging.py), so a logger bound
        # by an earlier test would bypass this capture. A fresh one binds inside it.
        patch("app.workers.tasks.log", structlog.get_logger()),
    ):
        event_id = await _ingest_calendar_event(data, diary.id, "UTC")
    return event_id, [entry for entry in logs if entry["event"] == "event_edit_redraft_queued"]


class TestEditRedraft:
    async def test_title_change_queues_one_redraft(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        entry = await _entry_with_event(db_session, diary)
        redraft = AsyncMock()

        event_id, lines = await _ingest(diary, _google(summary="Soccer final"), redraft)

        redraft.assert_awaited_once_with(entry.id)
        assert len(lines) == 1
        assert lines[0]["changed"] == ["summary"]
        assert lines[0]["entry_id"] == str(entry.id)
        assert lines[0]["event_id"] == event_id

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"location": "Field 4"}, "location"),
            ({"description": "Bring water and snacks"}, "description"),
            (
                {
                    "attendees": [
                        *STORED["attendees"],
                        {"displayName": "Sam", "email": "s@example.com"},
                    ]
                },
                "guests",
            ),
        ],
    )
    async def test_location_description_and_guest_changes_each_queue(
        self, client: AsyncClient, db_session: AsyncSession, change: dict, field: str
    ):
        diary = await _diary(db_session)
        entry = await _entry_with_event(db_session, diary)
        redraft = AsyncMock()

        _, lines = await _ingest(diary, _google(**change), redraft)

        redraft.assert_awaited_once_with(entry.id)
        assert [line["changed"] for line in lines] == [[field]]

    async def test_log_line_carries_no_event_text(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary)

        _, lines = await _ingest(
            diary, _google(summary="Secret party", description="At 12 Elm St"), AsyncMock()
        )

        text = repr(lines)
        assert "Secret party" not in text and "Elm St" not in text
        assert lines[0]["changed"] == ["summary", "description"]

    async def test_rsvp_only_change_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary)
        redraft = AsyncMock()
        rsvp = [{**STORED["attendees"][0], "responseStatus": "accepted"}]

        _, lines = await _ingest(diary, _google(attendees=rsvp), redraft)

        redraft.assert_not_awaited()
        assert lines == []

    async def test_identical_rescan_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary)
        redraft = AsyncMock()

        _, lines = await _ingest(diary, _google(), redraft)

        redraft.assert_not_awaited()
        assert lines == []

    async def test_published_entry_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary, status="published")
        redraft = AsyncMock()

        await _ingest(diary, _google(summary="Soccer final"), redraft)

        redraft.assert_not_awaited()

    async def test_deleted_entry_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary, deleted=True)
        redraft = AsyncMock()

        await _ingest(diary, _google(summary="Soccer final"), redraft)

        redraft.assert_not_awaited()

    async def test_event_with_no_entry_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await make_event(
            db_session,
            diary_id=diary.id,
            external_id="evt-1",
            payload=dict(STORED),
            occurred_at=ENDED_START,
            ends_at=ENDED_START + timedelta(hours=1),
        )
        redraft = AsyncMock()

        await _ingest(diary, _google(summary="Soccer final"), redraft)

        redraft.assert_not_awaited()

    async def test_edited_draft_still_queues(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        entry = await _entry_with_event(db_session, diary, body_source="user")
        redraft = AsyncMock()

        await _ingest(diary, _google(summary="Soccer final"), redraft)

        redraft.assert_awaited_once_with(entry.id)

    async def test_owner_pending_deletion_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary)
        owner = await db_session.get(User, diary.owner_user_id)
        assert owner is not None
        now = datetime.now(tz=UTC)
        owner.deleted_at = now
        owner.hard_delete_after = now + timedelta(days=7)
        await db_session.commit()
        redraft = AsyncMock()

        await _ingest(diary, _google(summary="Soccer final"), redraft)

        redraft.assert_not_awaited()

    async def test_move_to_future_with_edit_queues_at_most_once(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        entry = await _entry_with_event(db_session, diary, external_ids=("evt-1", "evt-2"))
        redraft = AsyncMock()
        later = datetime.now(tz=UTC) + timedelta(days=5)

        _, lines = await _ingest(diary, _google(start=later, summary="Soccer final"), redraft)

        # The move releases evt-1 and redrafts the entry for its remaining event.
        redraft.assert_awaited_once_with(entry.id)
        assert lines == [], "the edit belongs to an event that has left the entry"

    async def test_edit_to_an_event_moved_to_the_future_queues_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        entry = await _entry_with_event(db_session, diary)
        entry.creation_source = "calendar_pick"  # a move leaves a calendar pick attached
        await db_session.commit()
        redraft = AsyncMock()
        later = datetime.now(tz=UTC) + timedelta(days=5)

        _, lines = await _ingest(diary, _google(start=later, summary="Soccer final"), redraft)

        # The draft would describe an event that hasn't happened yet.
        redraft.assert_not_awaited()
        assert lines == []

    async def test_queue_failure_is_swallowed(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary(db_session)
        await _entry_with_event(db_session, diary)

        event_id, _ = await _ingest(
            diary, _google(summary="Soccer final"), AsyncMock(side_effect=RuntimeError("down"))
        )

        assert event_id is not None
        stored = (
            await db_session.execute(
                select(Event.payload)
                .where(Event.id == uuid.UUID(event_id))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert stored["summary"] == "Soccer final"
