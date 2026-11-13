"""Where an entry happened (PoC item 24).

First match wins: the parent's own choice, a photo's GPS, the location text
of the entry's calendar events, the diary's home place. A place the parent
set is never replaced by anything automatic.
Spec: docs/superpowers/specs/2026-11-12-per-entry-location-design.md
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Enrichment, Entry, EntryPhoto
from app.services.geocoding import place_from_event_text

_PRECISION = 6  # the columns are Numeric(9, 6)


def _rounded(lat, lon) -> tuple[float, float] | None:
    if lat is None or lon is None:
        return None
    return (round(float(lat), _PRECISION), round(float(lon), _PRECISION))


async def load_entry_for_location(db: AsyncSession, entry_id: uuid.UUID) -> Entry | None:
    """The entry with what its place is worked out from: photos, events, diary."""
    result = await db.execute(
        select(Entry)
        .where(Entry.id == entry_id)
        .options(
            selectinload(Entry.diary),
            selectinload(Entry.events),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
        )
    )
    return result.scalar_one_or_none()


async def _choose_place(entry: Entry) -> tuple[str | None, tuple[float, float] | None, str | None]:
    """(name, coordinates, source) for an entry without a parent-set place. Writes nothing."""
    photos = sorted(entry.entry_photos, key=lambda ep: (ep.position is None, ep.position or 0))
    for entry_photo in photos:
        photo = entry_photo.photo
        if photo is None or photo.deleted_at is not None:
            continue
        coords = _rounded(photo.lat, photo.lon)
        if coords:
            return None, coords, "photo"  # no name: Open-Meteo cannot turn GPS into a town

    events = sorted(entry.events, key=lambda e: e.occurred_at or datetime.min.replace(tzinfo=UTC))
    for event in events:
        text = ((event.payload or {}).get("location") or "").strip()
        if not text:
            continue
        place = await place_from_event_text(text)
        if place is not None:
            return place.label, _rounded(place.lat, place.lon), "event"

    if entry.diary is not None:
        home = _rounded(entry.diary.lat, entry.diary.lon)
        if home:
            return entry.diary.location_name, home, "home"
    return None, None, None


async def resolve_entry_location(entry: Entry, db: AsyncSession) -> bool:
    """Work out *entry*'s place. Returns True if its coordinates changed.

    *entry* must have `diary`, `events` and `entry_photos.photo` loaded
    (see load_entry_for_location). The geocoding happens first, with no lock;
    the row is then re-read under a lock, so a place the parent saved in the
    meantime is kept, and only real changes are written.
    """
    if entry.location_source == "parent":
        return False
    name, coords, source = await _choose_place(entry)

    # FOR NO KEY UPDATE: does not wait on weather rows being inserted for this entry.
    await db.refresh(
        entry,
        attribute_names=["location_name", "lat", "lon", "location_source"],
        with_for_update={"key_share": True},
    )
    if entry.location_source == "parent":
        return False
    before = _rounded(entry.lat, entry.lon)
    if coords != before:
        # Columns are Numeric: compare rounded values, or every run would rewrite them.
        entry.lat, entry.lon = coords if coords else (None, None)
    if entry.location_name != name:
        entry.location_name = name
    if entry.location_source != source:
        entry.location_source = source
    return coords != before


async def reset_weather(entry_id: uuid.UUID, db: AsyncSession) -> int:
    """Remove an entry's stored weather, so it is fetched again for a new place or dates."""
    result = await db.execute(
        delete(Enrichment).where(Enrichment.entry_id == entry_id, Enrichment.kind == "weather")
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def update_place_and_weather(
    entry_id: uuid.UUID, db: AsyncSession, *, refetch_weather: bool = False
) -> None:
    """Work out the entry's place, then make its weather match it.

    *refetch_weather*: the dates or a parent-set place changed, so stored
    weather is stale even if this run did not move the place.
    """
    from app.workers.enrichments import enrich_entry_weather

    entry = await load_entry_for_location(db, entry_id)
    if entry is None or entry.deleted_at is not None:
        return
    moved = await resolve_entry_location(entry, db)
    if moved or refetch_weather:
        await reset_weather(entry.id, db)
    coords = _rounded(entry.lat, entry.lon)
    # Commit before any weather call: the entry's row must not stay locked
    # while Open-Meteo answers (or times out).
    await db.commit()
    # Weather is kept only if the entry still has these coordinates when it is
    # stored: another job may have moved the place meanwhile.
    await enrich_entry_weather(entry_id, db, expected_coords=coords)
