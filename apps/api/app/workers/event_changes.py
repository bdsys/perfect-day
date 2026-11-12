"""What happens to an entry when one of its calendar events is cancelled or moved.

Text a person has written or published is never removed. An untouched AI draft
that a rule created exists only because of its events, so it follows them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entry, Event, RuleSeriesClaim

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


async def release_event_from_entry(db: AsyncSession, event: Event) -> uuid.UUID | None:
    """Take *event* off its entry and deal with what is left of the entry.

    An untouched rule-created draft with no other events is moved to trash,
    along with any series claim pointing at it. One that still has other events
    is kept, and its id is returned so the caller can queue a redraft once it
    has committed. Any other entry is left exactly as it is.

    Does not commit.
    """
    if event.entry_id is None:
        return None

    entry = (
        await db.execute(select(Entry).where(Entry.id == event.entry_id).with_for_update())
    ).scalar_one_or_none()
    event.entry_id = None
    if entry is None or not is_untouched_rule_draft(entry):
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
