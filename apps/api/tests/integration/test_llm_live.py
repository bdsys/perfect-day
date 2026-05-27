"""Live LLM golden test — only runs when ANTHROPIC_API_KEY is set.

Run with: make test-live
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def db_url_sync(pg):
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    return f"postgresql://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"


@pytest.fixture(scope="module", autouse=True)
def run_migrations(db_url_sync):
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url_sync)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def db_url(pg):
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    return f"postgresql+asyncpg://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"


@pytest_asyncio.fixture(scope="module")
async def engine(db_url):
    e = create_async_engine(db_url, echo=False)
    yield e
    await e.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncSession:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_diary(db: AsyncSession):
    from app.models import Diary, User

    user = User(
        email="live-test@example.com",
        password_hash="x",  # noqa: S106
        subscription_tier="free",
    )
    db.add(user)
    await db.flush()

    diary = Diary(
        owner_user_id=user.id,
        name="Live Test Diary",
        slug="live-test-diary",
        subject_name="Alex",
        subject_relation="child",
        tone_hint="warm",
        timezone="America/Chicago",
    )
    db.add(diary)
    await db.flush()
    return diary


async def _make_entry_with_events(db: AsyncSession, diary_id):
    from app.models import Entry, Event

    entry = Entry(
        diary_id=diary_id,
        entry_date=date(2026, 5, 10),
        status="draft",
        created_by="auto",
    )
    db.add(entry)
    await db.flush()

    events = [
        Event(
            entry_id=entry.id,
            source="google_calendar",
            external_id="evt-soccer-001",
            occurred_at=datetime(2026, 5, 10, 16, 0, tzinfo=UTC),
            payload={"summary": "Soccer practice", "location": "Main Field", "description": ""},
        ),
        Event(
            entry_id=entry.id,
            source="google_calendar",
            external_id="evt-dinner-001",
            occurred_at=datetime(2026, 5, 10, 19, 0, tzinfo=UTC),
            payload={"summary": "Pizza dinner", "location": "Home", "description": ""},
        ),
    ]
    for ev in events:
        db.add(ev)
    await db.flush()
    return entry, events


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live LLM test",
)
@pytest.mark.asyncio
async def test_generate_draft_golden(db: AsyncSession, db_url: str):
    """Generate a real draft entry via the Anthropic API and assert core invariants."""
    from sqlalchemy import select

    from app.models import Entry, LLMGeneration
    from app.workers.llm import generate_draft_for_entry

    diary = await _make_diary(db)
    entry, _events = await _make_entry_with_events(db, diary.id)
    entry_id = entry.id

    await db.commit()

    import app.workers.llm as worker_llm

    _live_engine = create_async_engine(db_url, echo=False)
    _live_factory = async_sessionmaker(_live_engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _test_db_session():
        async with _live_factory() as session:
            yield session
            await session.commit()

    with patch.object(worker_llm, "db_session", _test_db_session):
        await generate_draft_for_entry(entry_id)

    await _live_engine.dispose()

    result = await db.execute(select(Entry).where(Entry.id == entry_id))
    updated_entry = result.scalar_one()

    assert updated_entry.title is not None and len(updated_entry.title) > 0, (
        "Expected a non-empty title"
    )
    assert updated_entry.body_markdown is not None and len(updated_entry.body_markdown) > 0, (
        "Expected non-empty body_markdown"
    )

    gen_result = await db.execute(
        select(LLMGeneration)
        .where(LLMGeneration.entry_id == entry_id, LLMGeneration.status == "success")
        .order_by(LLMGeneration.created_at.desc())
        .limit(1)
    )
    gen = gen_result.scalar_one_or_none()
    assert gen is not None, "Expected an LLMGeneration audit row with status='success'"
    assert gen.input_tokens and gen.input_tokens > 0, "Expected non-zero input token count"
    assert gen.output_tokens and gen.output_tokens > 0, "Expected non-zero output token count"


# ---------------------------------------------------------------------------
# Gemini live tests — require GEMINI_API_KEY
# ---------------------------------------------------------------------------

_SKIP_GEMINI = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set — skipping Gemini live tests",
)


@pytest.mark.live
@_SKIP_GEMINI
@pytest.mark.asyncio
async def test_gemini_provider_direct_returns_parseable_json():
    """Call GeminiProvider.generate() directly and assert a parseable JSON response."""
    from app.workers.llm_providers import GeminiProvider

    system = (
        "Return ONLY valid JSON matching this schema exactly: "
        '{"title": string, "title_facts_used": [int], "body_markdown": string, "facts_used": [int]}'
    )
    diary_context = (
        "Subject: Alex\nSubject relation: child\nVoice: second (pronoun: you)\nTone: warm"
    )
    entry_data = (
        "DATE: 2026-05-10\n\nEVENTS:\n"
        '<event index="1">[google_calendar] 2026-05-10T16:00:00-07:00, '
        '"Soccer practice", location: "Main Field"</event>'
    )

    result = await GeminiProvider().generate(system, diary_context, entry_data)

    assert result.raw_text, "Expected non-empty raw_text"
    assert result.model.startswith("gemini-"), f"Expected gemini-* model id, got {result.model}"
    assert result.input_tokens and result.input_tokens > 0, "Expected non-zero input_tokens"
    assert result.output_tokens and result.output_tokens > 0, "Expected non-zero output_tokens"

    # Gemini sometimes wraps JSON in a ```json ... ``` code fence — strip it before parsing.
    raw = result.raw_text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    parsed = json.loads(raw)
    assert "title" in parsed, "Expected 'title' key in response"
    assert "body_markdown" in parsed, "Expected 'body_markdown' key in response"


@pytest.mark.live
@_SKIP_GEMINI
@pytest.mark.asyncio
async def test_anthropic_failure_falls_over_to_gemini(
    db_url: str, monkeypatch: pytest.MonkeyPatch
):
    """Bad ANTHROPIC_API_KEY + good GEMINI_API_KEY → entry generated by Gemini.

    Uses its own engine/sessions throughout to avoid the module-scoped engine's
    asyncpg pool being bound to a stale event loop (pytest-asyncio creates a fresh
    loop per test function, so shared module-scoped pools die after the first test).
    """
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.models import Entry, LLMGeneration
    from app.workers.llm import generate_draft_for_entry

    # Poison the Anthropic key so AnthropicProvider raises LLMPermanentError immediately.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-deliberately-invalid-key-for-failover-test")
    get_settings.cache_clear()

    _test_engine = create_async_engine(db_url, echo=False)
    _test_factory = async_sessionmaker(_test_engine, expire_on_commit=False, class_=AsyncSession)

    async with _test_factory() as setup_session:
        diary = await _make_diary(setup_session)
        entry, _events = await _make_entry_with_events(setup_session, diary.id)
        entry_id = entry.id
        await setup_session.commit()

    import app.workers.llm as worker_llm

    @asynccontextmanager
    async def _test_db_session():
        async with _test_factory() as session:
            yield session
            await session.commit()

    try:
        with patch.object(worker_llm, "db_session", _test_db_session):
            await generate_draft_for_entry(entry_id)

        async with _test_factory() as assert_session:
            result = await assert_session.execute(select(Entry).where(Entry.id == entry_id))
            updated_entry = result.scalar_one()

            assert updated_entry.body_source == "llm", (
                f"Expected body_source='llm' (Gemini success), got '{updated_entry.body_source}'"
            )
            assert updated_entry.title and len(updated_entry.title) > 0, (
                "Expected non-empty title"
            )
            assert updated_entry.body_markdown and len(updated_entry.body_markdown) > 0, (
                "Expected non-empty body_markdown"
            )

            gen_result = await assert_session.execute(
                select(LLMGeneration)
                .where(LLMGeneration.entry_id == entry_id, LLMGeneration.status == "success")
                .order_by(LLMGeneration.created_at.desc())
                .limit(1)
            )
            gen = gen_result.scalar_one_or_none()
            assert gen is not None, "Expected an LLMGeneration row with status='success'"
            assert gen.model.startswith("gemini-"), (
                f"Expected gemini-* model in LLMGeneration.model, got '{gen.model}'"
            )
            assert gen.input_tokens and gen.input_tokens > 0, "Expected non-zero input_tokens"
            assert gen.output_tokens and gen.output_tokens > 0, "Expected non-zero output_tokens"
    finally:
        await _test_engine.dispose()
        get_settings.cache_clear()
