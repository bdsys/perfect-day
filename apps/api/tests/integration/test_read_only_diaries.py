"""Diaries past the owner's plan limit are read-only; the oldest stay writable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import (
    AutoCreationRule,
    Diary,
    DiaryPermission,
    Entry,
    LLMGeneration,
    ScanJob,
    User,
)
from tests.fixtures.factories import make_entry, make_user

CONDITION = {"op": "AND", "children": [{"field": "title", "op": "contains", "value": "x"}]}


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _diary(db: AsyncSession, owner: User, name: str, age_days: int) -> Diary:
    d = Diary(
        owner_user_id=owner.id,
        name=name,
        slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}",
        timezone="UTC",
        tone_hint="warm",
        subject_relation="self",
        created_at=datetime.now(tz=UTC) - timedelta(days=age_days),
    )
    db.add(d)
    db.add(ScanJob(diary=d))
    await db.commit()
    await db.refresh(d)
    return d


class World:
    owner: User
    a: Diary
    b: Diary
    entry_a: Entry
    entry_b: Entry
    rule_b: AutoCreationRule


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.owner = await make_user(db_session, subscription_tier="free")
    w.a = await _diary(db_session, w.owner, "A", 10)
    w.b = await _diary(db_session, w.owner, "B", 5)
    w.entry_a = await make_entry(db_session, w.a)
    w.entry_b = await make_entry(db_session, w.b)
    w.rule_b = AutoCreationRule(
        diary_id=w.b.id, name="R", condition=CONDITION, options={}, enabled=True
    )
    db_session.add(w.rule_b)
    await db_session.commit()
    return w


def _assert_read_only(r) -> None:
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["detail"]["code"] == "tier_limit"
    assert body["detail"]["details"]["source"] == "diary_read_only"
    assert body["detail"]["details"]["limit"] == 1
    assert body["detail"]["details"]["current"] == 2


async def test_oldest_diary_stays_writable(client: AsyncClient, world: World) -> None:
    r = await client.patch(
        f"/v1/entries/{world.entry_a.id}", json={"title": "ok"}, headers=_auth(world.owner)
    )
    assert r.status_code == 200


def _guarded_requests(w: World) -> list[tuple[str, str, dict | None]]:
    d, e, rid = w.b.id, w.entry_b.id, w.rule_b.id
    return [
        ("POST", f"/v1/diaries/{d}/entries", {"entry_date": "2025-06-01"}),
        ("PATCH", f"/v1/entries/{e}", {"title": "t"}),
        ("POST", f"/v1/entries/{e}/publish", None),
        ("POST", f"/v1/entries/{e}/suggestion/accept", None),
        ("DELETE", f"/v1/entries/{e}/suggestion", None),
        ("POST", f"/v1/entries/{e}/unpublish", None),
        ("POST", f"/v1/entries/{e}/restore", None),
        ("POST", f"/v1/entries/{e}/regenerate", None),
        ("PATCH", f"/v1/diaries/{d}", {"name": "N"}),
        ("POST", f"/v1/diaries/{d}/scan/run", None),
        (
            "POST",
            f"/v1/diaries/{d}/scan/backfill",
            {"from_date": "2025-01-01", "to_date": "2025-01-31", "sources": ["google_calendar"]},
        ),
        ("POST", f"/v1/diaries/{d}/rules", {"name": "R2", "condition": CONDITION}),
        ("PATCH", f"/v1/rules/{rid}", {"name": "Renamed"}),
        ("DELETE", f"/v1/rules/{rid}", None),
        ("POST", f"/v1/diaries/{d}/rules/preview", {"condition": CONDITION}),
        ("POST", f"/v1/rules/{rid}/apply", {"days": 30}),
        (
            "POST",
            f"/v1/diaries/{d}/entries/from-event",
            {"event_id": str(uuid.uuid4())},
        ),
        ("POST", f"/v1/entries/{e}/photos", {"photo_id": str(uuid.uuid4())}),
        ("DELETE", f"/v1/entries/{e}/photos/{uuid.uuid4()}", None),
        ("POST", f"/v1/entries/{e}/photo-imports", None),
    ]


@pytest.mark.parametrize("index", range(20))
async def test_read_only_blocks_writes(client: AsyncClient, world: World, index: int) -> None:
    method, url, body = _guarded_requests(world)[index]
    r = await client.request(method, url, json=body, headers=_auth(world.owner))
    _assert_read_only(r)


async def test_read_only_allows_reads_and_deletes(client: AsyncClient, world: World) -> None:
    h = _auth(world.owner)
    assert (await client.get(f"/v1/entries/{world.entry_b.id}", headers=h)).status_code == 200
    assert (await client.delete(f"/v1/entries/{world.entry_b.id}", headers=h)).status_code == 204
    assert (await client.delete(f"/v1/diaries/{world.b.id}", headers=h)).status_code == 200


async def test_upgrade_reactivates_read_only_diary(
    client: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    world.owner.subscription_tier = "plus"
    await db_session.commit()
    r = await client.patch(
        f"/v1/entries/{world.entry_b.id}", json={"title": "back"}, headers=_auth(world.owner)
    )
    assert r.status_code == 200


async def test_editor_sees_owner_read_only(
    client: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    editor = await make_user(db_session)
    db_session.add(DiaryPermission(diary_id=world.b.id, user_id=editor.id, role="editor"))
    await db_session.commit()
    r = await client.patch(
        f"/v1/entries/{world.entry_b.id}", json={"title": "t"}, headers=_auth(editor)
    )
    _assert_read_only(r)


async def test_workers_skip_read_only(
    client: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    from app.workers import llm as worker_llm
    from app.workers.llm_providers import AnthropicProvider
    from app.workers.photo_imports import _can_still_edit
    from app.workers.tasks import _scan_diary

    generate = AsyncMock()
    with (
        patch.object(AnthropicProvider, "generate", new=generate),
        patch("app.workers.tasks.log") as log,
    ):
        await _scan_diary(str(world.b.id))
        await worker_llm.generate_draft_for_entry(world.entry_b.id)
    generate.assert_not_awaited()
    log.info.assert_any_call("diary_read_only_skip", diary_id=str(world.b.id), where="scan")

    count = (
        await db_session.execute(
            select(func.count()).select_from(LLMGeneration).where(
                LLMGeneration.entry_id == world.entry_b.id
            )
        )
    ).scalar_one()
    assert count == 0

    assert await _can_still_edit(db_session, world.entry_b.id, world.owner.id) is False
    assert await _can_still_edit(db_session, world.entry_a.id, world.owner.id) is True


async def test_beat_dispatches_only_writable_diaries(
    client: AsyncClient, world: World
) -> None:
    from unittest.mock import MagicMock

    from app.workers.beat_tasks import _dispatch_due_scans

    delay = MagicMock()
    with patch("app.workers.tasks.scan_diary.delay", new=delay):
        await _dispatch_due_scans()
    dispatched = {c.args[0] for c in delay.call_args_list}
    assert dispatched == {str(world.a.id)}


async def test_read_only_wins_over_backfill_limit(client: AsyncClient, world: World) -> None:
    """R2: a Free owner's read-only diary says read-only, not "backfill is Plus"."""
    r = await client.post(
        f"/v1/diaries/{world.b.id}/scan/backfill",
        json={"from_date": "2025-01-01", "to_date": "2025-01-31"},
        headers=_auth(world.owner),
    )
    _assert_read_only(r)


async def _run_on_read_only_diary(db_session: AsyncSession, world: World, status: str):
    from datetime import date

    from app.models import BackfillRun

    run = BackfillRun(
        diary_id=world.b.id,
        from_date=date(2025, 1, 1),
        to_date=date(2025, 1, 31),
        sources=["google_calendar"],
        status=status,
        error="earlier" if status == "cancelled" else None,
    )
    db_session.add(run)
    await db_session.commit()
    return run


async def test_read_only_backfill_run_finishes(
    client: AsyncClient, db_session: AsyncSession, world: World) -> None:
    from app.models import BackfillRun
    from app.workers.tasks import _backfill_diary

    run = await _run_on_read_only_diary(db_session, world, "pending")
    run_id = run.id
    await _backfill_diary(str(run_id))
    db_session.expire_all()
    rows = await db_session.execute(select(BackfillRun).where(BackfillRun.id == run_id))
    run = rows.scalar_one()
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error == "This diary is read-only on your plan, so the backfill didn't run."


async def test_cancelled_backfill_run_stays_cancelled_when_read_only(
    client: AsyncClient,
    db_session: AsyncSession, world: World
) -> None:
    from app.models import BackfillRun
    from app.workers.tasks import _backfill_diary

    run = await _run_on_read_only_diary(db_session, world, "cancelled")
    run_id = run.id
    await _backfill_diary(str(run_id))
    db_session.expire_all()
    rows = await db_session.execute(select(BackfillRun).where(BackfillRun.id == run_id))
    run = rows.scalar_one()
    assert run.status == "cancelled"
    assert run.error == "earlier"
