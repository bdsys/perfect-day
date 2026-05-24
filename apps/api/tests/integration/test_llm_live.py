"""Live LLM golden test — only runs when ANTHROPIC_API_KEY is set.

Run with: make test-live
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
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
    from alembic import command
    from alembic.config import Config

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
        password_hash="x",
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
            occurred_at=datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc),
            payload={"summary": "Soccer practice", "location": "Main Field", "description": ""},
        ),
        Event(
            entry_id=entry.id,
            source="google_calendar",
            external_id="evt-dinner-001",
            occurred_at=datetime(2026, 5, 10, 19, 0, tzinfo=timezone.utc),
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
    from app.workers.llm import generate_draft_for_entry, validate_citation

    diary = await _make_diary(db)
    entry, events = await _make_entry_with_events(db, diary.id)
    entry_id = entry.id

    await db.commit()

    # Patch the db_session helper to reuse our test DB
    engine_url = db_url

    import app.workers.utils as worker_utils

    original_db_session = worker_utils.db_session

    # Run against the test DB by patching db_session to our engine
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    _live_engine = create_async_engine(engine_url, echo=False)
    _live_factory = async_sessionmaker(_live_engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _test_db_session():
        async with _live_factory() as session:
            yield session
            await session.commit()

    with patch.object(worker_utils, "db_session", _test_db_session):
        await generate_draft_for_entry(entry_id)

    await _live_engine.dispose()

    # Re-fetch the entry using our test DB session
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
