"""Automatic redrafts of one entry are coalesced to one per 10 minutes (18.1-h)."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry
from tests.fixtures.factories import make_diary, make_event, make_user

SOCCER_RULE = {
    "op": "AND",
    "children": [{"field": "title", "op": "contains", "value": "Soccer", "case_sensitive": False}],
}


@pytest_asyncio.fixture
async def entry_id(client: AsyncClient):
    """A fresh entry id whose redraft key is gone before and after the test."""
    from app.core.dependencies import get_redis

    e = uuid.uuid4()
    key = f"redraft_pending:{e}"
    await get_redis().delete(key)
    yield e
    await get_redis().delete(key)


class TestQueueRedraft:
    async def test_two_redrafts_in_window_queue_one(self, entry_id):
        from app.workers.tasks import queue_redraft

        with patch("app.workers.tasks.generate_entry_draft") as task:
            await queue_redraft(entry_id)
            await queue_redraft(entry_id)

        task.apply_async.assert_called_once_with(
            (str(entry_id),), {"coalesced": True}, countdown=600
        )
        task.delay.assert_not_called()

    async def test_coalesced_task_clears_key(self, entry_id):
        from app.core.dependencies import get_redis
        from app.workers.tasks import _generate_entry_draft, queue_redraft

        with patch("app.workers.tasks.generate_entry_draft") as task:
            await queue_redraft(entry_id)
        assert await get_redis().exists(f"redraft_pending:{entry_id}")

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", AsyncMock()),
            patch("app.workers.utils.db_session", MagicMock()),
        ):
            await _generate_entry_draft(str(entry_id), coalesced=True)
        assert not await get_redis().exists(f"redraft_pending:{entry_id}")

        with patch("app.workers.tasks.generate_entry_draft") as again:
            await queue_redraft(entry_id)
        again.apply_async.assert_called_once()
        assert task.apply_async.call_count == 1

    async def test_key_is_cleared_before_the_entry_is_read(self, entry_id):
        from app.core.dependencies import get_redis
        from app.workers.tasks import _generate_entry_draft, queue_redraft

        with patch("app.workers.tasks.generate_entry_draft"):
            await queue_redraft(entry_id)

        seen: list[bool] = []

        async def _draft(*args, **kwargs):
            seen.append(bool(await get_redis().exists(f"redraft_pending:{entry_id}")))

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", _draft),
            patch("app.workers.utils.db_session", MagicMock()),
        ):
            await _generate_entry_draft(str(entry_id), coalesced=True)
        assert seen == [False]

    async def test_regenerate_does_not_clear_key(self, entry_id):
        from app.core.dependencies import get_redis
        from app.workers.tasks import _generate_entry_draft, queue_redraft

        with patch("app.workers.tasks.generate_entry_draft"):
            await queue_redraft(entry_id)

        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", AsyncMock()),
            patch("app.workers.utils.db_session", MagicMock()),
        ):
            await _generate_entry_draft(str(entry_id), "regenerate")
        assert await get_redis().exists(f"redraft_pending:{entry_id}")

    async def test_broker_down_releases_key_and_raises(self, entry_id):
        from app.core.dependencies import get_redis
        from app.workers.tasks import queue_redraft

        with patch("app.workers.tasks.generate_entry_draft") as task:
            task.apply_async.side_effect = ConnectionError("broker down")
            with pytest.raises(ConnectionError):
                await queue_redraft(entry_id)
        assert not await get_redis().exists(f"redraft_pending:{entry_id}")

        with patch("app.workers.tasks.generate_entry_draft") as again:
            await queue_redraft(entry_id)
        again.apply_async.assert_called_once()

    async def test_redis_down_queues_directly(self, entry_id):
        from app.workers.tasks import queue_redraft

        with (
            patch("app.core.dependencies.get_redis", side_effect=ConnectionError("down")),
            patch("app.workers.tasks.generate_entry_draft") as task,
        ):
            await queue_redraft(entry_id)

        task.delay.assert_called_once_with(str(entry_id))
        task.apply_async.assert_not_called()

    async def test_old_message_without_coalesced_runs(self, entry_id):
        """A message queued before this change has no coalesced flag and must still run."""
        from app.core.dependencies import get_redis
        from app.workers.tasks import _generate_entry_draft, generate_entry_draft, queue_redraft

        params = inspect.signature(generate_entry_draft.run).parameters
        assert params["coalesced"].default is False
        assert params["trigger"].default == "background"

        with patch("app.workers.tasks.generate_entry_draft"):
            await queue_redraft(entry_id)

        draft = AsyncMock()
        with (
            patch("app.services.entry_location.update_place_and_weather", AsyncMock()),
            patch("app.workers.llm.generate_draft_for_entry", draft),
            patch("app.workers.utils.db_session", MagicMock()),
        ):
            await _generate_entry_draft(str(entry_id))

        assert draft.await_args.kwargs["trigger"] == "background"
        assert await get_redis().exists(f"redraft_pending:{entry_id}"), "a stray run keeps the key"


class TestCallSites:
    async def _diary_with_two_event_entry(self, db: AsyncSession):
        user = await make_user(db)
        diary = await make_diary(db, owner=user, timezone="UTC")
        db.add(
            AutoCreationRule(
                diary_id=diary.id,
                name="Soccer",
                enabled=True,
                condition=SOCCER_RULE,
                options={"recurring": "per_instance", "multi_day": "per_day"},
            )
        )
        entry = Entry(
            diary_id=diary.id,
            entry_date=datetime(2026, 6, 1, tzinfo=UTC).date(),
            title="Soccer practice",
            body_markdown="Practice ran from ten until eleven.",
            body_source="llm",
            status="draft",
            created_by="auto",
            creation_source="rule",
        )
        db.add(entry)
        await db.commit()
        for ext in ("evt-1", "evt-2"):
            await make_event(
                db, diary_id=diary.id, entry=entry, external_id=ext,
                ends_at=datetime.now(tz=UTC) - timedelta(days=1),
                rules_evaluated_at=datetime.now(tz=UTC) - timedelta(hours=1),
            )  # fmt: skip
        return diary, entry

    async def test_cancel_and_reschedule_use_queue_redraft(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _cancel_calendar_event, _ingest_calendar_event

        diary, entry = await self._diary_with_two_event_entry(db_session)

        with (
            patch("app.workers.tasks.queue_redraft", AsyncMock()) as queue,
            patch("app.workers.tasks.generate_entry_draft") as task,
        ):
            await _cancel_calendar_event("evt-1", str(diary.id))
        queue.assert_awaited_once_with(entry.id)
        task.delay.assert_not_called()

        start = datetime.now(tz=UTC) + timedelta(days=5)
        moved = {
            "id": "evt-2",
            "summary": "Soccer practice",
            "status": "confirmed",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }
        # evt-1 is gone, so add another event to keep the entry alive on the move.
        await make_event(db_session, diary_id=diary.id, entry=entry, external_id="evt-3")
        with (
            patch("app.workers.tasks.queue_redraft", AsyncMock()) as queue,
            patch("app.workers.tasks.generate_entry_draft") as task,
            patch("app.workers.tasks.evaluate_rules_for_event"),
        ):
            await _ingest_calendar_event(moved, diary.id, "UTC")
        queue.assert_awaited_once_with(entry.id)
        task.delay.assert_not_called()
        assert (await db_session.execute(select(Entry.id))).scalars().all() == [entry.id]
