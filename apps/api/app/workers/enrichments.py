"""Enrichment orchestrator.

Drives per-entry weather enrichment: resolves a lat/lon for the entry
(preferring photo EXIF, falling back to diary location), iterates each
calendar day covered by the entry, and calls open_meteo.fetch_daily for
any day not already stored. Results are written with ON CONFLICT DO NOTHING
so the function is safe to retry.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Enrichment, Entry, EntryPhoto
from app.workers import open_meteo

log = logging.getLogger(__name__)

_DATE_CAP = 30


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-testable)
# ---------------------------------------------------------------------------


def _iter_entry_dates(entry: Any) -> Generator[date, None, None]:
    """Yield each calendar date covered by the entry, capped at _DATE_CAP."""
    start: date = entry.entry_date
    end: date = entry.entry_end_date if entry.entry_end_date is not None else start

    total_days = (end - start).days + 1
    if total_days > _DATE_CAP:
        log.warning(
            "entry_date_range_capped",
            extra={
                "entry_id": str(entry.id),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "capped_at": _DATE_CAP,
            },
        )
        total_days = _DATE_CAP

    for i in range(total_days):
        yield start + timedelta(days=i)


def _resolve_lat_lon(entry: Any) -> tuple[float, float] | None:
    """Return (lat, lon) for the entry.

    Priority:
    1. First photo in entry.photos that has non-None lat/lon (EXIF data).
    2. entry.diary.lat / entry.diary.lon (diary home location).
    3. None if neither source has coordinates.

    ``entry.photos`` must be an iterable of objects with ``lat`` and ``lon``
    attributes (may be Decimal or float). When calling from
    ``enrich_entry_weather``, pass a view object with
    ``photos = [ep.photo for ep in entry.entry_photos]``.
    """
    photos = getattr(entry, "photos", [])
    for photo in photos:
        if photo.lat is not None and photo.lon is not None:
            return (float(photo.lat), float(photo.lon))

    diary = getattr(entry, "diary", None)
    if diary is not None and diary.lat is not None and diary.lon is not None:
        return (float(diary.lat), float(diary.lon))

    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _existing_dates(db: AsyncSession, entry_id: uuid.UUID) -> set[date]:
    """Return the set of dates already enriched for this entry (kind='weather')."""
    result = await db.execute(
        select(Enrichment.captured_for_at)
        .where(Enrichment.entry_id == entry_id, Enrichment.kind == "weather")
    )
    rows = result.scalars().all()
    # captured_for_at is DateTime(timezone=True); extract the date portion.
    return {row.date() for row in rows if row is not None}


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def enrich_entry_weather(
    entry_id: uuid.UUID, db: AsyncSession
) -> tuple[int, int, int]:
    """Fetch and store weather enrichments for every day an entry covers.

    Returns:
        (inserted, skipped_existing, failed_or_no_location)
    """
    # Load entry with diary and entry_photos->photo for lat/lon resolution.
    result = await db.execute(
        select(Entry)
        .where(Entry.id == entry_id)
        .options(
            selectinload(Entry.diary),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        log.warning("enrich_entry_weather_entry_not_found", extra={"entry_id": str(entry_id)})
        return (0, 0, 0)

    # Build a view compatible with _resolve_lat_lon (which expects .photos with lat/lon).
    view = SimpleNamespace(
        id=entry.id,
        entry_date=entry.entry_date,
        entry_end_date=entry.entry_end_date,
        photos=[ep.photo for ep in entry.entry_photos if ep.photo is not None],
        diary=entry.diary,
    )

    coords = _resolve_lat_lon(view)
    if coords is None:
        log.info(
            "enrich_entry_weather_no_location",
            extra={"entry_id": str(entry_id)},
        )
        total = sum(1 for _ in _iter_entry_dates(view))
        return (0, 0, total)

    lat, lon = coords
    already_done = await _existing_dates(db, entry_id)

    inserted = 0
    skipped = 0
    failed = 0

    for day in _iter_entry_dates(view):
        if day in already_done:
            skipped += 1
            continue

        payload = await open_meteo.fetch_daily(lat, lon, day)
        if payload is None:
            log.warning(
                "enrich_entry_weather_fetch_failed",
                extra={"entry_id": str(entry_id), "date": day.isoformat()},
            )
            failed += 1
            continue

        # captured_for_at stored as midnight UTC for the target date.
        captured_for_at = datetime(day.year, day.month, day.day, tzinfo=UTC)

        stmt = (
            insert(Enrichment)
            .values(
                entry_id=entry_id,
                kind="weather",
                payload=payload,
                source="open_meteo",
                captured_for_at=captured_for_at,
                fetched_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_enrichments_entry_kind_captured")
        )
        insert_result: CursorResult[Any] = await db.execute(stmt)  # type: ignore[assignment]
        if insert_result.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    await db.commit()
    return (inserted, skipped, failed)
