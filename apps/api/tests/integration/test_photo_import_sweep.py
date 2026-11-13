"""The sweep that ends Google Photos imports whose worker chain died (item 14)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import PhotoImport
from tests.integration.test_photo_imports import _owner_with_entry, _user


async def _make_import(
    client,
    db_session,
    *,
    status: str,
    expires_in: timedelta | None = None,
    idle: timedelta | None = None,
) -> uuid.UUID:
    """A photo_imports row; *idle* back-dates updated_at (the trigger is paused for it)."""
    now = datetime.now(tz=UTC)  # fresh: the full suite runs for minutes
    prefix = f"sw-{uuid.uuid4().hex[:8]}"
    _, entry = await _owner_with_entry(client, db_session, prefix, connected=False)
    user = await _user(db_session, f"{prefix}@example.com")
    imp = PhotoImport(
        user_id=user.id,
        entry_id=uuid.UUID(entry["id"]),
        google_session_id="sess-sweep",
        status=status,
        expires_at=now + expires_in if expires_in is not None else None,
    )
    db_session.add(imp)
    await db_session.commit()
    if idle is not None:
        trg = "trg_photo_imports_updated_at"
        await db_session.execute(text(f"ALTER TABLE photo_imports DISABLE TRIGGER {trg}"))
        await db_session.execute(
            text("UPDATE photo_imports SET updated_at = :t WHERE id = :i"),
            {"t": now - idle, "i": imp.id},
        )
        await db_session.execute(text(f"ALTER TABLE photo_imports ENABLE TRIGGER {trg}"))
        await db_session.commit()
    return imp.id


async def _status(db_session, import_id: uuid.UUID) -> str:
    db_session.expire_all()
    return (
        await db_session.execute(select(PhotoImport.status).where(PhotoImport.id == import_id))
    ).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expires_in", "idle", "expected"),
    [
        # waiting, past expires_at + 5 min grace -> expired
        ("waiting", timedelta(minutes=-6), None, "expired"),
        # waiting, inside the grace window: the worker's own expiry still has time to run
        ("waiting", timedelta(minutes=-4), None, "waiting"),
        # waiting, not yet expired
        ("waiting", timedelta(minutes=10), None, "waiting"),
        # copying with no progress for over 30 minutes -> failed
        ("copying", None, timedelta(minutes=31), "failed"),
        # copying that made progress recently is left alone
        ("copying", None, timedelta(minutes=5), "copying"),
        # terminal imports are never touched, however old
        ("done", timedelta(minutes=-600), timedelta(hours=5), "done"),
        ("cancelled", timedelta(minutes=-600), timedelta(hours=5), "cancelled"),
        ("needs_reconnect", timedelta(minutes=-600), timedelta(hours=5), "needs_reconnect"),
    ],
)
async def test_sweep_transitions(client, db_session, status, expires_in, idle, expected):
    from app.workers.photo_imports import sweep_stuck_imports

    import_id = await _make_import(
        client, db_session, status=status, expires_in=expires_in, idle=idle
    )

    await sweep_stuck_imports()

    assert await _status(db_session, import_id) == expected


@pytest.mark.asyncio
async def test_sweep_counts_what_it_ended(client, db_session):
    from app.workers.photo_imports import sweep_stuck_imports

    await _make_import(client, db_session, status="waiting", expires_in=timedelta(minutes=-10))
    await _make_import(client, db_session, status="copying", idle=timedelta(hours=2))
    await _make_import(client, db_session, status="copying", idle=timedelta(minutes=1))

    assert await sweep_stuck_imports() == 2


def test_beat_schedule_includes_photo_import_sweep():
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sweep-stuck-photo-imports"]
    assert entry["task"] == "app.workers.beat_tasks.sweep_stuck_photo_imports"
    assert entry["schedule"].total_seconds() == 10 * 60
