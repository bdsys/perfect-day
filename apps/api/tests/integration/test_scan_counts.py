"""A scan records how many entries it made and what started it (TR-1, C-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Diary, Event, ScanJob, ScanRun
from tests.fixtures.factories import make_diary, make_event, make_user

SOCCER_RULE = {
    "op": "AND",
    "children": [
        {"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": False}
    ],
}
PER_INSTANCE = {"recurring": "per_instance", "multi_day": "per_day"}
PER_SERIES = {"recurring": "per_series", "multi_day": "per_day"}


async def _diary_with_rule(db: AsyncSession, options: dict = PER_INSTANCE) -> Diary:
    user = await make_user(db)
    diary = await make_diary(db, owner=user, timezone="UTC")
    db.add(
        AutoCreationRule(
            diary_id=diary.id, name="Soccer", enabled=True, condition=SOCCER_RULE,
            options=options,
        )
    )
    await db.commit()
    return diary


def _soccer_payload(day: datetime, **extra) -> dict:
    return {
        **extra,
        "summary": "Soccer practice",
        "description": "",
        "location": "",
        "start": {"dateTime": day.isoformat()},
        "end": {"dateTime": (day + timedelta(hours=1)).isoformat()},
        "status": "confirmed",
        "attendees": [],
    }


async def _due_soccer_events(
    db: AsyncSession, diary: Diary, days_ago: list[int], **extra
) -> list[Event]:
    """Stored before they ended, now over, rules not run yet."""
    events = []
    for n in days_ago:
        start = (datetime.now(tz=UTC) - timedelta(days=n)).replace(microsecond=0)
        events.append(
            await make_event(
                db,
                diary_id=diary.id,
                payload=_soccer_payload(start, **extra),
                occurred_at=start,
                ends_at=start + timedelta(hours=1),
            )
        )
    return events


async def _only_run(db: AsyncSession, diary: Diary) -> ScanRun:
    return (
        await db.execute(
            select(ScanRun)
            .where(ScanRun.diary_id == diary.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_scan_counts_entries(client: AsyncClient, db_session: AsyncSession):
    from app.workers.tasks import _scan_diary

    diary = await _diary_with_rule(db_session)
    await _due_soccer_events(db_session, diary, days_ago=[3, 2])  # two days, two entries

    with patch("app.workers.tasks.generate_entry_draft") as draft:
        await _scan_diary(str(diary.id))

    run = await _only_run(db_session, diary)
    assert run.entries_created == 2
    assert run.triggered_by == "beat"
    assert draft.delay.call_count == 2


async def test_manual_scan_is_marked_manual(client: AsyncClient, db_session: AsyncSession):
    from app.workers.tasks import _scan_diary

    r = await client.post(
        "/v1/auth/register", json={"email": "manual-scan@example.com", "password": "Password1!"}
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()

    with patch("app.workers.tasks.scan_diary.delay") as queued:
        r = await client.post(f"/v1/diaries/{diary['id']}/scan/run", headers=auth)
    assert r.status_code == 202
    queued.assert_called_once_with(
        diary["id"], past_days=None, future_days=None, triggered_by="manual"
    )

    # Run what was queued, as the worker would.
    call = queued.call_args
    await _scan_diary(*call.args, **call.kwargs)

    run = (
        await db_session.execute(
            select(ScanRun).where(ScanRun.diary_id == uuid.UUID(diary["id"]))
        )
    ).scalar_one()
    assert run.triggered_by == "manual"


async def test_one_failing_event_does_not_stop_the_others(
    client: AsyncClient, db_session: AsyncSession
):
    """The failed event keeps rules_evaluated_at NULL, so the next scan tries it again."""
    import app.workers.tasks as tasks

    diary = await _diary_with_rule(db_session)
    bad, good = await _due_soccer_events(db_session, diary, days_ago=[3, 2])
    real_evaluate = tasks._evaluate_rules_for_event

    async def _evaluate(event_id: str, diary_id: str) -> None:
        if event_id == str(bad.id):
            raise RuntimeError("boom")
        await real_evaluate(event_id, diary_id)

    with (
        patch("app.workers.tasks._evaluate_rules_for_event", AsyncMock(side_effect=_evaluate)),
        patch("app.workers.tasks.generate_entry_draft"),
    ):
        entry_ids = await tasks.evaluate_due_events(diary.id)

    stored = {
        e.id: e
        for e in (
            await db_session.execute(
                select(Event)
                .where(Event.diary_id == diary.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert entry_ids == {stored[good.id].entry_id}
    assert stored[bad.id].rules_evaluated_at is None
    assert stored[bad.id].entry_id is None


async def test_rules_failure_is_recorded_and_counts_nothing(
    client: AsyncClient, db_session: AsyncSession
):
    from app.workers.tasks import _scan_diary

    diary = await _diary_with_rule(db_session)
    with patch(
        "app.workers.tasks.evaluate_due_events",
        AsyncMock(side_effect=ConnectionError("db unreachable")),
    ):
        await _scan_diary(str(diary.id))

    run = await _only_run(db_session, diary)
    assert run.entries_created == 0
    assert run.status == "partial"
    assert [e["source"] for e in run.errors] == ["rules"]


async def test_scan_ignores_events_from_other_diaries(
    client: AsyncClient, db_session: AsyncSession
):
    from app.workers.tasks import evaluate_due_events

    diary = await _diary_with_rule(db_session)
    other = await _diary_with_rule(db_session)
    await _due_soccer_events(db_session, other, days_ago=[2])

    with patch("app.workers.tasks.generate_entry_draft"):
        assert await evaluate_due_events(diary.id) == set()

    other_event = (
        await db_session.execute(select(Event).where(Event.diary_id == other.id))
    ).scalar_one()
    assert other_event.rules_evaluated_at is None


async def test_event_joining_an_existing_series_entry_counts_zero(
    client: AsyncClient, db_session: AsyncSession
):
    from app.workers.tasks import _scan_diary
    from app.workers.utils import db_session as worker_db

    diary = await _diary_with_rule(db_session, options=PER_SERIES)
    series = {"recurringEventId": "soccer-series"}
    await _due_soccer_events(db_session, diary, days_ago=[14], **series)

    with patch("app.workers.tasks.generate_entry_draft"):
        await _scan_diary(str(diary.id))
        first = await _only_run(db_session, diary)
        assert first.entries_created == 1

        # A week later the next practice is over; it joins the series entry.
        await _due_soccer_events(db_session, diary, days_ago=[7], **series)
        async with worker_db() as db:
            job = await db.execute(select(ScanJob).where(ScanJob.diary_id == diary.id))
            job.scalar_one().next_scan_after = None
        await _scan_diary(str(diary.id))

    runs = (
        await db_session.execute(
            select(ScanRun)
            .where(ScanRun.diary_id == diary.id)
            .order_by(ScanRun.started_at)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    assert [r.entries_created for r in runs] == [1, 0]
    events = (await db_session.execute(select(Event).where(Event.diary_id == diary.id))).scalars()
    assert len({e.entry_id for e in events}) == 1  # both practices share the one entry
