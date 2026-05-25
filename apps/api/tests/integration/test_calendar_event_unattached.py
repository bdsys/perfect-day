"""Integration test: ingest_calendar_event stores Event with entry_id=NULL after the refactor."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Diary, Event, User
from app.workers.tasks import _ingest_calendar_event


@pytest.fixture()
def sample_event_data() -> dict:
    return {
        "id": "abc123",
        "summary": "Soccer practice",
        "description": "At the park",
        "location": "City Park",
        "status": "confirmed",
        "start": {"dateTime": "2026-05-25T10:00:00-05:00"},
        "end": {"dateTime": "2026-05-25T11:30:00-05:00"},
        "attendees": [
            {"displayName": "Alice", "email": "alice@example.com", "responseStatus": "accepted"}
        ],
    }


@pytest.fixture(autouse=True)
def wire_worker_db(db_url):
    """Point the worker's db_session at the test database engine."""
    import app.core.database as db_module

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    original_engine = db_module._engine
    original_factory = db_module._session_factory

    db_module._engine = engine
    db_module._session_factory = factory

    yield

    db_module._engine = original_engine
    db_module._session_factory = original_factory


@pytest_asyncio.fixture
async def diary_id(db_session: AsyncSession) -> uuid.UUID:
    """Create a minimal User + Diary and return the diary's UUID."""
    user = User(email=f"worker-test-{uuid.uuid4()}@example.com")
    db_session.add(user)
    await db_session.flush()

    diary = Diary(
        owner_user_id=user.id,
        name="Test Diary",
        slug=f"test-{uuid.uuid4()}",
        timezone="America/Chicago",
    )
    db_session.add(diary)
    await db_session.flush()
    await db_session.commit()
    return diary.id


class TestIngestCalendarEventUnattached:
    async def test_new_event_has_null_entry_id(
        self, db_session: AsyncSession, diary_id, sample_event_data
    ):
        """After refactor, ingest_calendar_event must NOT create an Entry."""
        with patch("app.workers.tasks.evaluate_rules_for_event") as mock_rules:
            mock_rules.delay = MagicMock()
            result_id = await _ingest_calendar_event(sample_event_data, diary_id, "America/Chicago")

        # Return value must be a valid UUID string
        assert result_id is not None
        uuid.UUID(result_id)  # raises ValueError if not a valid UUID

        result = await db_session.execute(select(Event).where(Event.external_id == "abc123"))
        event = result.scalar_one_or_none()
        assert event is not None, "Event row must be created"
        assert event.entry_id is None, "entry_id must be NULL — no Entry auto-created"
        assert event.diary_id == diary_id, "diary_id must be set"
        assert event.source == "google_calendar"
        assert event.external_id == "abc123"
        assert event.payload["summary"] == "Soccer practice"

        # Rule evaluation must be queued with the correct event and diary IDs
        mock_rules.delay.assert_called_once_with(str(event.id), str(diary_id))

    async def test_duplicate_event_updates_payload(
        self, db_session: AsyncSession, diary_id, sample_event_data
    ):
        """Re-ingesting the same external_id updates payload but keeps entry_id unchanged."""
        with patch("app.workers.tasks.evaluate_rules_for_event") as mock_rules:
            mock_rules.delay = MagicMock()
            await _ingest_calendar_event(sample_event_data, diary_id, "America/Chicago")
            sample_event_data["summary"] = "Soccer practice UPDATED"
            await _ingest_calendar_event(sample_event_data, diary_id, "America/Chicago")

        result = await db_session.execute(select(Event).where(Event.external_id == "abc123"))
        event = result.scalar_one()  # fails loudly if more than one row exists
        assert event.payload["summary"] == "Soccer practice UPDATED"
        assert event.entry_id is None

        # Duplicate ingest returns early (before the .delay() call), so rule evaluation
        # is only queued for genuinely new events — assert exactly 1 call.
        mock_rules.delay.assert_called_once()
