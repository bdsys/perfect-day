"""What happens to an entry when one of its calendar events is cancelled or moved.

Text a person has written or published is never removed. An untouched AI draft
that a rule or a calendar pick created exists only because of its events, so
it follows a cancelled event. A moved event leaves a calendar-pick draft alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, EntryPhoto, Event, Photo, RuleSeriesClaim

log = structlog.get_logger()

# body_source values that mean no person has edited the text.
MACHINE_ONLY_BODY_SOURCES = frozenset({"llm", "fallback"})


def is_untouched_rule_draft(entry: Entry) -> bool:
    """True for a draft a rule created whose text is still exactly what the AI wrote."""
    return (
        entry.status == "draft"
        and entry.deleted_at is None
        and entry.creation_source == "rule"
        and entry.body_source in MACHINE_ONLY_BODY_SOURCES
    )


def is_untouched_auto_draft(entry: Entry) -> bool:
    """True for a rule or calendar-pick draft whose body_source says no person has edited the text.

    Whether a person attached photos is a separate question; see entry_has_photos."""
    return (
        entry.status == "draft"
        and entry.deleted_at is None
        and entry.creation_source in ("rule", "calendar_pick")
        and entry.body_source in MACHINE_ONLY_BODY_SOURCES
    )


async def entry_has_photos(db: AsyncSession, entry_id: uuid.UUID) -> bool:
    """True if the entry has at least one attached photo that is not deleted."""
    count = (
        await db.execute(
            select(func.count())
            .select_from(EntryPhoto)
            .join(Photo, Photo.id == EntryPhoto.photo_id)
            .where(EntryPhoto.entry_id == entry_id, Photo.deleted_at.is_(None))
        )
    ).scalar_one()
    return count > 0


async def release_event_from_entry(
    db: AsyncSession, event: Event, *, photos_count_as_touched: bool = False
) -> uuid.UUID | None:
    """Take *event* off its entry and deal with what is left of the entry.

    An untouched rule or calendar-pick draft with no other events is moved to trash,
    along with any series claim pointing at it. One that still has other events
    is kept, and its id is returned so the caller can queue a redraft once it
    has committed. Any other entry is left exactly as it is. With
    photos_count_as_touched, a draft that has an attached photo is also left as it is.

    Does not commit.
    """
    if event.entry_id is None:
        return None

    entry = (
        await db.execute(select(Entry).where(Entry.id == event.entry_id).with_for_update())
    ).scalar_one_or_none()
    event.entry_id = None
    if entry is None or not is_untouched_auto_draft(entry):
        return None
    if photos_count_as_touched and await entry_has_photos(db, entry.id):
        return None

    other_events = (
        await db.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.entry_id == entry.id, Event.id != event.id)
        )
    ).scalar_one()
    if other_events:
        return entry.id

    entry.deleted_at = datetime.now(tz=UTC)
    # A later instance of the series would otherwise be attached to the trashed entry.
    await db.execute(delete(RuleSeriesClaim).where(RuleSeriesClaim.entry_id == entry.id))
    log.info("untouched_draft_removed", entry_id=str(entry.id), event_id=str(event.id))
    return None
