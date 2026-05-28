"""Endpoints for listing unattached calendar events and creating entries from them."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Entry, Event, User
from app.routers.v1.diaries import _get_diary_or_404
from app.routers.v1.entries import EntryOut, _entry_out_from_orm
from app.services.tier import enforce_entry_tier_limit
from app.workers.tasks import generate_entry_draft

router = APIRouter(tags=["calendar_events"])


class CalendarEventSummary(BaseModel):
    id: uuid.UUID
    summary: str
    description: str
    location: str
    occurred_at: datetime | None
    start: dict
    end: dict
    attendees: list[dict]
    status: str

    model_config = {"from_attributes": False}


class CreateFromEventBody(BaseModel):
    event_id: uuid.UUID


def _calendar_event_summary(event: Event) -> CalendarEventSummary:
    p = event.payload or {}
    return CalendarEventSummary(
        id=event.id,
        summary=p.get("summary", ""),
        description=p.get("description", ""),
        location=p.get("location", ""),
        occurred_at=event.occurred_at,
        start=p.get("start", {}),
        end=p.get("end", {}),
        attendees=p.get("attendees", []),
        status=p.get("status", ""),
    )


@router.get(
    "/diaries/{diary_id}/calendar-events",
    response_model=list[CalendarEventSummary],
)
async def list_calendar_events(
    diary_id: uuid.UUID,
    attached: bool = Query(False),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarEventSummary]:
    """List events synced to this diary. By default returns only unattached events."""
    await _get_diary_or_404(diary_id, user, db)

    q = select(Event).where(Event.diary_id == diary_id)
    if not attached:
        q = q.where(Event.entry_id.is_(None))
    if from_date:
        q = q.where(Event.occurred_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.where(Event.occurred_at <= datetime.combine(to_date, datetime.max.time()))
    q = q.order_by(Event.occurred_at.desc()).limit(limit)

    result = await db.execute(q)
    return [_calendar_event_summary(e) for e in result.scalars()]


@router.post(
    "/diaries/{diary_id}/entries/from-event",
    response_model=EntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry_from_event(
    diary_id: uuid.UUID,
    body: CreateFromEventBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    from app.workers.tz_utils import google_event_to_entry_date

    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    event_result = await db.execute(
        select(Event).where(Event.id == body.event_id).with_for_update()
    )
    event = event_result.scalar_one_or_none()

    if event is None or event.diary_id != diary_id:
        raise HTTPException(status_code=404, detail="event_not_found")

    if event.entry_id is not None:
        raise HTTPException(status_code=409, detail="event_already_attached")

    await enforce_entry_tier_limit(
        owner_user_id=user.id,
        source="manual",
        db=db,
        owner_subscription_tier=user.subscription_tier,
    )

    p = event.payload or {}
    raw_event = {"start": p.get("start", {}), "end": p.get("end", {})}
    entry_date, entry_end_date = google_event_to_entry_date(raw_event, diary.timezone)
    if entry_date is None:
        from datetime import date as _date
        entry_date = event.occurred_at.date() if event.occurred_at else _date.today()

    entry = Entry(
        diary_id=diary_id,
        entry_date=entry_date,
        entry_end_date=entry_end_date,
        status="draft",
        created_by="manual",
        creation_source="calendar_pick",
    )
    db.add(entry)
    await db.flush()

    event.entry_id = entry.id
    await db.refresh(entry, ["events", "rule_matches", "llm_generations"])

    try:
        generate_entry_draft.delay(str(entry.id))
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Failed to queue generate_entry_draft for entry %s", entry.id
        )

    return _entry_out_from_orm(entry)
