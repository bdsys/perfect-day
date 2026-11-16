"""AI drafts are counted per owner per month, reserved before the LLM call, and
released when no AI text is saved (item 18, spec "What counts as an AI draft").
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.workers.llm as worker_llm
from app.models import AiDraftSpend, DiaryPermission, Entry, LLMGeneration, User
from app.services.ai_drafts import (
    AI_DRAFT_LIMIT_ERROR,
    ai_drafts_left,
    drafts_used,
    release_lost_ai_drafts,
    reserve_ai_draft,
    usage_period,
)
from app.workers.llm_providers import LLMPermanentError, LLMResult
from tests.fixtures.factories import make_diary, make_entry, make_event, make_user

_PICNIC = {
    "summary": "Picnic in the park",
    "location": "",
    "description": "",
    "start": {"dateTime": "2026-05-19T12:00:00+00:00"},
    "end": {"dateTime": "2026-05-19T13:00:00+00:00"},
    "status": "confirmed",
    "attendees": [],
}

_REPLY = json.dumps({
    "title": "Picnic day",
    "title_facts_used": [],
    "body_markdown": "A picnic in the park.",
    "facts_used": [1],
})


def _provider(name: str = "anthropic", *, fail: bool = False, before_reply=None) -> MagicMock:
    async def generate(system_prompt, diary_context, entry_data):
        if before_reply is not None:
            await before_reply()
        if fail:
            raise LLMPermanentError("mocked failure")
        return LLMResult(raw_text=_REPLY, input_tokens=10, output_tokens=5, model="claude-opus-5-5")

    p = MagicMock()
    p.name = name
    p.is_configured.return_value = True
    p.generate = AsyncMock(side_effect=generate)
    return p


def _unconfigured(name: str = "gemini") -> MagicMock:
    p = MagicMock()
    p.name = name
    p.is_configured.return_value = False
    p.generate = AsyncMock()
    return p


@asynccontextmanager
async def _worker_db(db_url: str):
    """The drafting worker's sessions point at the test database."""
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def session():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    try:
        with patch.object(worker_llm, "db_session", session):
            yield
    finally:
        await engine.dispose()


async def _run_draft(
    db_url: str, entry_id: uuid.UUID, anthropic: MagicMock, gemini: MagicMock | None = None,
    **kwargs,
) -> None:
    async with _worker_db(db_url):
        with (
            patch.object(worker_llm, "AnthropicProvider", return_value=anthropic),
            patch.object(worker_llm, "GeminiProvider", return_value=gemini or _unconfigured()),
        ):
            await worker_llm.generate_draft_for_entry(entry_id, **kwargs)


async def _month(db: AsyncSession, owner: User) -> str:
    return (await usage_period(db, owner.id)).month


async def _used(db: AsyncSession, owner: User) -> int:
    return await drafts_used(db, owner.id, await _month(db, owner))


async def _spend(db: AsyncSession, owner: User, n: int) -> None:
    for _ in range(n):
        assert await reserve_ai_draft(db, owner=owner, entry_id=None, key=uuid.uuid4().hex)
    await db.commit()


async def _reload(db: AsyncSession, entry_id: uuid.UUID) -> Entry:
    result = await db.execute(
        select(Entry).where(Entry.id == entry_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _generations(db: AsyncSession, entry_id: uuid.UUID) -> list[LLMGeneration]:
    result = await db.execute(select(LLMGeneration).where(LLMGeneration.entry_id == entry_id))
    return list(result.scalars())


async def _owner_with_entry(
    db: AsyncSession, *, body: str | None = None, body_source: str = "llm",
    tier: str = "free",
) -> tuple[User, Entry]:
    owner = await make_user(db, subscription_tier=tier)
    diary = await make_diary(db, owner, timezone="UTC")
    entry = await make_entry(
        db, diary, entry_date=date(2026, 5, 19), title=None, body_markdown=body
    )
    entry.body_source = body_source
    await db.commit()
    await make_event(db, entry=entry, payload=_PICNIC)
    return owner, entry


# ---------------------------------------------------------------------------
# The usage service
# ---------------------------------------------------------------------------


async def test_month_follows_oldest_diary_timezone(db_session: AsyncSession) -> None:
    now = datetime(2026, 12, 1, 7, 30, tzinfo=UTC)
    no_diary = await make_user(db_session)
    period = await usage_period(db_session, no_diary.id, now=now)
    assert period.month == "2026-12"
    assert period.resets_on == date(2027, 1, 1)

    owner = await make_user(db_session)
    await make_diary(db_session, owner, timezone="America/Los_Angeles")
    period = await usage_period(db_session, owner.id, now=now)
    assert period.month == "2026-11"
    assert period.resets_on == date(2026, 12, 1)


async def test_reserve_until_limit(db_session: AsyncSession) -> None:
    owner = await make_user(db_session, subscription_tier="free")
    await make_diary(db_session, owner)
    results = [
        await reserve_ai_draft(db_session, owner=owner, entry_id=None, key=f"k{i}")
        for i in range(6)
    ]
    await db_session.commit()
    assert results == [True] * 5 + [False]
    assert await _used(db_session, owner) == 5
    assert await ai_drafts_left(db_session, owner) == 0


async def test_same_key_reserves_once(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    assert await reserve_ai_draft(db_session, owner=owner, entry_id=None, key="task-1")
    assert await reserve_ai_draft(db_session, owner=owner, entry_id=None, key="task-1")
    await db_session.commit()
    assert await _used(db_session, owner) == 1


async def test_concurrent_reservations_never_exceed_limit(
    db_engine, db_session: AsyncSession
) -> None:
    owner = await make_user(db_session, subscription_tier="free")
    await make_diary(db_session, owner)
    await _spend(db_session, owner, 4)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def reserve(i: int) -> bool:
        async with factory() as s:
            ok = await reserve_ai_draft(s, owner=owner, entry_id=None, key=f"race-{i}")
            # Hold the transaction open a moment so the racers overlap.
            await asyncio.sleep(0.05)
            await s.commit()
            return ok

    results = await asyncio.gather(*(reserve(i) for i in range(3)))
    assert sorted(results) == [False, False, True]
    assert await _used(db_session, owner) == 5


async def test_deleting_entry_keeps_usage(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    entry = await make_entry(db_session, diary)
    assert await reserve_ai_draft(db_session, owner=owner, entry_id=entry.id, key="k")
    await db_session.commit()

    await db_session.execute(delete(Entry).where(Entry.id == entry.id))
    await db_session.commit()

    assert await _used(db_session, owner) == 1
    spend = (
        await db_session.execute(
            select(AiDraftSpend).where(AiDraftSpend.key == "k").execution_options(
                populate_existing=True
            )
        )
    ).scalar_one()
    assert spend.entry_id is None


async def test_plus_records_usage_and_downgrade_shows_zero(db_session: AsyncSession) -> None:
    owner = await make_user(db_session, subscription_tier="plus")
    await make_diary(db_session, owner)
    assert await ai_drafts_left(db_session, owner) is None
    await _spend(db_session, owner, 20)
    assert await _used(db_session, owner) == 20

    owner.subscription_tier = "free"
    await db_session.commit()
    assert await ai_drafts_left(db_session, owner) == 0


# ---------------------------------------------------------------------------
# The drafting worker
# ---------------------------------------------------------------------------


async def test_successful_draft_spends_one(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _run_draft(db_url, entry.id, _provider(), reservation_key="celery-task-1")

    assert (await _reload(db_session, entry.id)).body_source == "llm"
    assert await _used(db_session, owner) == 1
    spend = (await db_session.execute(select(AiDraftSpend))).scalar_one()
    assert spend.key == "celery-task-1"
    assert spend.entry_id == entry.id


async def test_failed_draft_releases(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _run_draft(db_url, entry.id, _provider(fail=True), _provider("gemini", fail=True))

    reloaded = await _reload(db_session, entry.id)
    assert reloaded.body_source == "fallback"
    assert await _used(db_session, owner) == 0


async def test_discarded_draft_releases(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)

    async def delete_entry_first() -> None:
        await db_session.execute(
            update(Entry).where(Entry.id == entry.id).values(deleted_at=datetime.now(UTC))
        )
        await db_session.commit()

    await _run_draft(db_url, entry.id, _provider(before_reply=delete_entry_first))

    reloaded = await _reload(db_session, entry.id)
    assert (reloaded.title, reloaded.body_markdown) == (None, None)
    assert await _used(db_session, owner) == 0


async def test_limit_writes_fallback_and_marker(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _spend(db_session, owner, 5)
    before = (await _reload(db_session, entry.id)).updated_at
    provider = _provider()

    await _run_draft(db_url, entry.id, provider)

    provider.generate.assert_not_awaited()
    reloaded = await _reload(db_session, entry.id)
    assert reloaded.body_source == "fallback"
    assert reloaded.title == "Picnic in the park"
    assert reloaded.body_markdown == "- 12:00–13:00  Picnic in the park"
    assert reloaded.updated_at > before
    [gen] = await _generations(db_session, entry.id)
    assert (gen.status, gen.model, gen.error, gen.mode) == (
        "failed", "none", AI_DRAFT_LIMIT_ERROR, "events"
    )
    assert AI_DRAFT_LIMIT_ERROR == "ai_draft_limit"
    assert await _used(db_session, owner) == 5


async def test_limit_leaves_parents_text(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(
        db_session, body="We went to the park.", body_source="user"
    )
    await _spend(db_session, owner, 5)
    provider = _provider()

    await _run_draft(db_url, entry.id, provider, trigger="regenerate")

    provider.generate.assert_not_awaited()
    reloaded = await _reload(db_session, entry.id)
    assert (reloaded.title, reloaded.body_markdown, reloaded.body_source) == (
        None, "We went to the park.", "user"
    )
    assert reloaded.suggested_body_markdown is None
    [gen] = await _generations(db_session, entry.id)
    assert (gen.status, gen.model, gen.error, gen.mode) == (
        "failed", "none", AI_DRAFT_LIMIT_ERROR, "hybrid"
    )


async def test_limit_replaces_stale_ai_text(db_url: str, db_session: AsyncSession) -> None:
    # An untouched AI draft lost an event (e.g. a cancelled swim lesson) and a
    # background redraft was queued. At the limit the old AI prose must not stay.
    owner, entry = await _owner_with_entry(
        db_session, body="A swim lesson, then a picnic in the park.", body_source="llm"
    )
    await _spend(db_session, owner, 5)
    provider = _provider()

    await _run_draft(db_url, entry.id, provider, trigger="background")

    provider.generate.assert_not_awaited()
    reloaded = await _reload(db_session, entry.id)
    assert reloaded.body_source == "fallback"
    assert reloaded.title == "Picnic in the park"
    assert reloaded.body_markdown == "- 12:00–13:00  Picnic in the park"
    [gen] = await _generations(db_session, entry.id)
    assert (gen.status, gen.model, gen.error, gen.mode) == (
        "failed", "none", AI_DRAFT_LIMIT_ERROR, "events"
    )
    assert await _used(db_session, owner) == 5


async def test_editor_regenerate_spends_owner_quota(
    db_url: str, db_session: AsyncSession
) -> None:
    owner, entry = await _owner_with_entry(db_session)
    editor = await make_user(db_session, subscription_tier="family")
    db_session.add(DiaryPermission(diary_id=entry.diary_id, user_id=editor.id, role="editor"))
    await db_session.commit()

    await _run_draft(db_url, entry.id, _provider(), trigger="regenerate")

    spends = (await db_session.execute(select(AiDraftSpend))).scalars().all()
    assert [s.user_id for s in spends] == [owner.id]


async def _prior_success(db: AsyncSession, entry: Entry) -> None:
    db.add(
        LLMGeneration(
            entry_id=entry.id, model="m", prompt_hash="h", status="success", mode="events"
        )
    )
    await db.commit()


async def test_background_redraft_free_at_limit(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(
        db_session, body="A swim lesson.", body_source="llm"
    )
    await _prior_success(db_session, entry)
    await _spend(db_session, owner, 5)
    provider = _provider()

    await _run_draft(db_url, entry.id, provider, trigger="background", reservation_key="task-9")

    provider.generate.assert_awaited()
    assert (await _reload(db_session, entry.id)).body_source == "llm"
    assert await _used(db_session, owner) == 5
    new = [g for g in await _generations(db_session, entry.id) if g.model == "claude-opus-5-5"]
    assert [(g.status, g.reservation_key) for g in new] == [("success", None)]


async def test_first_background_draft_blocked_at_limit(
    db_url: str, db_session: AsyncSession
) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _spend(db_session, owner, 5)
    provider = _provider()

    await _run_draft(db_url, entry.id, provider, trigger="background")

    provider.generate.assert_not_awaited()
    [gen] = await _generations(db_session, entry.id)
    assert gen.error == AI_DRAFT_LIMIT_ERROR
    assert await _used(db_session, owner) == 5


async def test_regenerate_counts_even_with_prior_draft(
    db_url: str, db_session: AsyncSession
) -> None:
    owner, entry = await _owner_with_entry(
        db_session, body="A swim lesson.", body_source="llm"
    )
    await _prior_success(db_session, entry)
    await _spend(db_session, owner, 4)

    await _run_draft(db_url, entry.id, _provider(), trigger="regenerate")

    assert await _used(db_session, owner) == 5


async def test_free_redraft_keeps_parents_text(db_url: str, db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(
        db_session, body="We went to the park.", body_source="user"
    )
    await _prior_success(db_session, entry)
    await _spend(db_session, owner, 5)

    provider = _provider()

    await _run_draft(db_url, entry.id, provider, trigger="background")

    # The free path ran: the model was called and no limit marker was written.
    provider.generate.assert_awaited()
    gens = await _generations(db_session, entry.id)
    assert not [g for g in gens if g.error == AI_DRAFT_LIMIT_ERROR]
    new = [g for g in gens if g.model == "claude-opus-5-5"]
    assert [(g.status, g.reservation_key) for g in new] == [("success", None)]
    reloaded = await _reload(db_session, entry.id)
    assert (reloaded.body_markdown, reloaded.body_source) == ("We went to the park.", "user")
    assert await _used(db_session, owner) == 5


# ---------------------------------------------------------------------------
# The sweeper for drafts lost to a killed worker
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


async def _seed_spend(
    db: AsyncSession, owner: User, entry: Entry | None, key: str, *, age_min: int,
    sweepable: bool = True,
) -> None:
    db.add(
        AiDraftSpend(
            user_id=owner.id,
            entry_id=entry.id if entry else None,
            month="2026-11",
            key=key,
            sweepable=sweepable,
            created_at=_NOW - timedelta(minutes=age_min),
        )
    )
    await db.commit()


async def _seed_success(db: AsyncSession, entry: Entry, key: str) -> None:
    db.add(
        LLMGeneration(
            entry_id=entry.id, model="m", prompt_hash="h", status="success", mode="events",
            reservation_key=key,
        )
    )
    await db.commit()


async def _spend_keys(db: AsyncSession) -> set[str]:
    return set((await db.execute(select(AiDraftSpend.key))).scalars())


async def test_sweeper_releases_old_orphan(db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _seed_spend(db_session, owner, entry, "s1", age_min=31)
    assert await release_lost_ai_drafts(db_session, now=_NOW) == 1
    await db_session.commit()
    assert await _spend_keys(db_session) == set()


async def test_sweeper_keeps_fresh_spend(db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _seed_spend(db_session, owner, entry, "s1", age_min=29)
    assert await release_lost_ai_drafts(db_session, now=_NOW) == 0
    assert await _spend_keys(db_session) == {"s1"}


async def test_sweeper_keeps_spend_with_its_success(db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _seed_spend(db_session, owner, entry, "s1", age_min=60)
    await _seed_success(db_session, entry, "s1")
    assert await release_lost_ai_drafts(db_session, now=_NOW) == 0
    assert await _spend_keys(db_session) == {"s1"}


async def test_sweeper_releases_killed_draft_after_retry_succeeds(
    db_session: AsyncSession,
) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _seed_spend(db_session, owner, entry, "s1", age_min=40)
    await _seed_spend(db_session, owner, entry, "s2", age_min=35)
    await _seed_success(db_session, entry, "s2")
    assert await release_lost_ai_drafts(db_session, now=_NOW) == 1
    await db_session.commit()
    assert await _spend_keys(db_session) == {"s2"}


async def test_sweeper_keeps_unsweepable_and_null_entry(db_session: AsyncSession) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _seed_spend(db_session, owner, entry, "old", age_min=90, sweepable=False)
    await _seed_spend(db_session, owner, None, "gone", age_min=90)
    assert await release_lost_ai_drafts(db_session, now=_NOW) == 0
    assert await _spend_keys(db_session) == {"old", "gone"}


async def test_successful_draft_records_its_reservation_key(
    db_url: str, db_session: AsyncSession
) -> None:
    owner, entry = await _owner_with_entry(db_session)
    await _run_draft(db_url, entry.id, _provider(), reservation_key="k1")

    gens = await _generations(db_session, entry.id)
    assert [g.reservation_key for g in gens if g.status == "success"] == ["k1"]
    spend = (await db_session.execute(select(AiDraftSpend))).scalar_one()
    assert spend.sweepable is True


async def test_suggestion_records_its_reservation_key(
    db_url: str, db_session: AsyncSession
) -> None:
    # A person's own text with events is a hybrid draft, which lands as a suggestion.
    owner, entry = await _owner_with_entry(
        db_session, body="We went to the park.", body_source="user"
    )
    await _run_draft(db_url, entry.id, _provider(), trigger="regenerate", reservation_key="k2")

    reloaded = await _reload(db_session, entry.id)
    assert reloaded.suggested_body_markdown is not None
    gens = await _generations(db_session, entry.id)
    assert [g.reservation_key for g in gens if g.status == "success"] == ["k2"]
    spend = (await db_session.execute(select(AiDraftSpend))).scalar_one()
    assert spend.sweepable is True
