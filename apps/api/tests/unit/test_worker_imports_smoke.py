"""Smoke tests that catch deferred-import errors in worker modules.

Background: a previous bug had `from sqlalchemy import select, selectinload`
inside a function body in `app.workers.llm` (selectinload lives in
`sqlalchemy.orm`). Module-load tests didn't catch it because Python only
evaluates function-body imports when the function is called. The result:
every Celery `generate_entry_draft` task crashed on ImportError before
doing any work, and no test exercised that path.

This file's job: cheaply invoke every worker entry point with mocks so
deferred imports get resolved, surfacing bad imports at test time.

These tests do NOT call the LLM or hit the network — they patch the
shared `db_session` and Redis dependencies so worker functions hit their
not-found early-return branches without needing infrastructure.
"""

from __future__ import annotations

import importlib
import pkgutil
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-load smoke test
# ---------------------------------------------------------------------------


def test_all_worker_modules_import_cleanly():
    """Every module under app.workers must import without raising.

    Catches: typos in top-level imports, missing dependencies, syntax errors.
    Does NOT catch: deferred imports inside function bodies (see tests below).
    """
    import app.workers as workers_pkg

    failures: list[tuple[str, str]] = []
    for mod_info in pkgutil.iter_modules(workers_pkg.__path__):
        mod_name = f"app.workers.{mod_info.name}"
        try:
            importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 — we want to catch literally everything
            failures.append((mod_name, f"{type(exc).__name__}: {exc}"))

    assert not failures, "Worker modules failed to import:\n" + "\n".join(
        f"  {name}: {err}" for name, err in failures
    )


# ---------------------------------------------------------------------------
# Deferred-import smoke tests
#
# Each Celery task's async helper has imports inside the function body
# (`from sqlalchemy import ...`, `from app.models import ...`). Those imports
# are only evaluated when the function is called. We invoke each helper with
# inputs that trigger an early return (entity-not-found path) so the import
# block runs but the function exits before doing real work.
# ---------------------------------------------------------------------------


def _make_empty_db_session():
    """Build a fake db_session() context manager whose queries return None.

    Each helper does `async with db_session() as db: result = await db.execute(...)`
    then `result.scalar_one_or_none()` — returning None triggers the not-found
    early-return path that exits before any real DB or LLM work.
    """
    fake_session = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    fake_session.execute = AsyncMock(return_value=fake_result)
    fake_session.commit = AsyncMock()
    fake_session.flush = AsyncMock()
    fake_session.add = MagicMock()

    @asynccontextmanager
    async def _fake_db_session():
        yield fake_session

    return _fake_db_session


@pytest.fixture
def patch_db_session(monkeypatch):
    """Patch `db_session` at its source AND on any module that already imported
    it at module level.

    Most worker helpers use deferred imports (`from app.workers.utils import
    db_session` inside a function body), so patching the source module is
    enough.  But `app.workers.llm` imports `db_session` at module level, which
    means Python has already bound the name on that module object by the time
    the fixture runs.  We must patch both to cover both patterns.
    """
    fake = _make_empty_db_session()
    import app.workers.llm as llm_mod
    import app.workers.utils as utils_mod

    monkeypatch.setattr(utils_mod, "db_session", fake)
    monkeypatch.setattr(llm_mod, "db_session", fake)
    return fake


@pytest.mark.asyncio
async def test_generate_draft_for_entry_imports_resolve(patch_db_session):
    """Calling generate_draft_for_entry with a non-existent ID must not raise ImportError.

    This is the test that would have caught the original
    `from sqlalchemy import selectinload` bug. The function body's deferred
    imports run, then the not-found branch returns cleanly.
    """
    from app.workers.llm import generate_draft_for_entry

    # Should return None cleanly (entry not found path) without raising.
    await generate_draft_for_entry(uuid.uuid4())


@pytest.mark.asyncio
async def test_scan_diary_imports_resolve(monkeypatch, patch_db_session):
    """Same protection for the scan path's deferred imports.

    Bypass the Redis lock by making `set` return False (lock not acquired
    → early return) so we exercise imports without needing Redis.
    """
    import app.core.dependencies as deps_mod

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)
    fake_redis.delete = AsyncMock()

    def _fake_get_redis():
        return fake_redis

    monkeypatch.setattr(deps_mod, "get_redis", _fake_get_redis)

    from app.workers.tasks import _scan_diary

    await _scan_diary(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_ingest_calendar_event_imports_resolve(patch_db_session):
    """Same protection for the ingest path's deferred imports."""
    from app.workers.tasks import _ingest_calendar_event

    # Provide an event_data with no start time → google_event_to_entry_date
    # returns None → function returns None before any DB or external work.
    result = await _ingest_calendar_event(
        event_data={"id": "evt1", "summary": "x"},
        diary_id=uuid.uuid4(),
        diary_timezone="UTC",
    )
    assert result is None


@pytest.mark.asyncio
async def test_backfill_diary_imports_resolve(patch_db_session):
    """Same protection for the backfill path's deferred imports."""
    from app.workers.tasks import _backfill_diary

    # BackfillRun lookup returns None → early return.
    await _backfill_diary(str(uuid.uuid4()))

