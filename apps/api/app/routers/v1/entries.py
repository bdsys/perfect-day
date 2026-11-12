from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import (
    Diary,
    Entry,
    EntryPhoto,
    EntryRuleMatch,
    Event,
    User,
)
from app.routers.v1.diaries import _get_diary_or_404
from app.services.flagged_words import still_present
from app.services.tier import enforce_entry_tier_limit

router = APIRouter(tags=["entries"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EntryCreate(BaseModel):
    entry_date: date
    entry_end_date: date | None = None
    title: str | None = None
    body_markdown: str | None = None

    @model_validator(mode="after")
    def _end_not_before_start(self) -> EntryCreate:
        if self.entry_end_date is not None and self.entry_end_date < self.entry_date:
            raise ValueError("entry_end_date must be on or after entry_date")
        return self


class EntryPatch(BaseModel):
    title: str | None = None
    body_markdown: str | None = None
    entry_date: date | None = None
    entry_end_date: date | None = None

    @model_validator(mode="after")
    def _entry_date_not_null(self) -> EntryPatch:
        # entry_date is NOT NULL; an explicit null would otherwise surface as a 500.
        if "entry_date" in self.model_fields_set and self.entry_date is None:
            raise ValueError("entry_date cannot be null")
        return self


class EventOut(BaseModel):
    id: uuid.UUID
    source: str
    occurred_at: datetime | None
    summary: str
    description: str
    location: str
    start: dict
    end: dict
    attendees: list[dict]
    status: str

    model_config = {"from_attributes": False}  # built manually from payload


class RuleMatchOut(BaseModel):
    rule_id: uuid.UUID
    rule_name: str
    matched_at: datetime

    model_config = {"from_attributes": False}


class LLMGenerationSummaryOut(BaseModel):
    id: uuid.UUID
    status: str  # "success" | "failed"
    error: str | None
    created_at: datetime
    mode: str   # "events" | "polish" | "hybrid" | "none"
    model: str | None

    model_config = ConfigDict(from_attributes=True)


class EnrichmentOut(BaseModel):
    id: uuid.UUID
    kind: str
    source: str | None
    payload: dict
    captured_for_at: datetime | None
    fetched_at: datetime
    model_config = {"from_attributes": True}


class SuggestionOut(BaseModel):
    """The AI's pending version of an entry that already has text."""

    title: str | None
    body_markdown: str
    flagged_tokens: list[str]
    body_source: str
    created_at: datetime


class EntryOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    entry_date: date
    entry_end_date: date | None
    title: str | None
    body_markdown: str | None
    flagged_tokens: list[str] | None
    status: str
    created_by: str
    published_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    body_source: str = "llm"
    creation_source: str = "manual"
    events: list[EventOut] = []
    rule_matches: list[RuleMatchOut] = []
    last_generation: LLMGenerationSummaryOut | None = None
    photos: list = []
    enrichments: list[EnrichmentOut] = []
    suggestion: SuggestionOut | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# ORM → schema helpers
# ---------------------------------------------------------------------------


def _event_out_from_orm(event: Event) -> EventOut:
    p = event.payload or {}
    return EventOut(
        id=event.id,
        source=event.source,
        occurred_at=event.occurred_at,
        summary=p.get("summary", ""),
        description=p.get("description", ""),
        location=p.get("location", ""),
        start=p.get("start", {}),
        end=p.get("end", {}),
        attendees=p.get("attendees", []),
        status=p.get("status", ""),
    )


def _entry_out_from_orm(entry: Entry, *, can_edit: bool = False) -> EntryOut:
    """The API view of *entry*. A pending AI suggestion is shown only to editors."""
    from app.routers.v1.photos import _photo_out

    events_out = sorted(
        [_event_out_from_orm(e) for e in entry.events],
        key=lambda e: e.occurred_at or datetime.min.replace(tzinfo=UTC),
    )
    rule_matches_out = [
        RuleMatchOut(
            rule_id=match.rule_id,
            rule_name=match.rule.name if match.rule else "",
            matched_at=match.matched_at,
        )
        for match in entry.rule_matches
    ]
    gens = entry.llm_generations
    last_gen: LLMGenerationSummaryOut | None = None
    if gens:
        latest = sorted(gens, key=lambda g: g.created_at, reverse=True)[0]
        last_gen = LLMGenerationSummaryOut(
            id=latest.id,
            status=latest.status,
            error=latest.error,
            created_at=latest.created_at,
            mode=latest.mode,
            model=latest.model,
        )
    photos_out = [
        _photo_out(ep.photo)
        for ep in sorted(
            entry.entry_photos,
            key=lambda ep: (ep.position is None, ep.position or 0),
        )
        if ep.photo is not None and ep.photo.deleted_at is None
    ]
    enrichments_out = [EnrichmentOut.model_validate(e) for e in entry.enrichments]
    suggestion_out: SuggestionOut | None = None
    if can_edit and entry.suggested_body_markdown is not None:
        suggestion_out = SuggestionOut(
            title=entry.suggested_title,
            body_markdown=entry.suggested_body_markdown,
            flagged_tokens=entry.suggested_flagged_tokens or [],
            body_source=entry.suggested_body_source or "llm",
            created_at=entry.suggested_at or entry.updated_at,
        )
    return EntryOut(
        id=entry.id,
        diary_id=entry.diary_id,
        entry_date=entry.entry_date,
        entry_end_date=entry.entry_end_date,
        title=entry.title,
        body_markdown=entry.body_markdown,
        flagged_tokens=entry.flagged_tokens,
        status=entry.status,
        created_by=entry.created_by,
        published_at=entry.published_at,
        deleted_at=entry.deleted_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        body_source=entry.body_source,
        creation_source=entry.creation_source,
        events=events_out,
        rule_matches=rule_matches_out,
        last_generation=last_gen,
        photos=photos_out,
        enrichments=enrichments_out,
        suggestion=suggestion_out,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_text(entry: Entry) -> str:
    return f"{entry.title or ''}\n{entry.body_markdown or ''}"


def _clear_suggestion(entry: Entry) -> None:
    entry.suggested_title = None
    entry.suggested_body_markdown = None
    entry.suggested_flagged_tokens = None
    entry.suggested_body_source = None
    entry.suggested_at = None


async def _get_entry_or_404(
    entry_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    require_editor: bool = False,
) -> tuple[Entry, Diary, str | None]:
    result = await db.execute(
        select(Entry)
        .options(
            selectinload(Entry.events),
            selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule),
            selectinload(Entry.llm_generations),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
            selectinload(Entry.enrichments),
        )
        .where(Entry.id == entry_id, Entry.deleted_at.is_(None))
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="not_found")

    diary, role = await _get_diary_or_404(entry.diary_id, user, db)

    if require_editor and role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    # Viewers cannot see drafts
    if entry.status == "draft" and role == "viewer":
        raise HTTPException(status_code=404, detail="not_found")

    return entry, diary, role


# Timeline order, newest first. `id` is the final tie-breaker so the order is
# total and a cursor always resumes at exactly one place.
ENTRY_TIMELINE_ORDER = (Entry.entry_date.desc(), Entry.created_at.desc(), Entry.id.desc())


def _after_cursor(entry_date: date, created_at: datetime, entry_id: uuid.UUID):
    """Predicate for entries that sort strictly after the given one in timeline order."""
    return or_(
        Entry.entry_date < entry_date,
        and_(Entry.entry_date == entry_date, Entry.created_at < created_at),
        and_(
            Entry.entry_date == entry_date,
            Entry.created_at == created_at,
            Entry.id < entry_id,
        ),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/diaries/{diary_id}/entries/trash", response_model=list[EntryOut])
async def list_deleted_entries(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EntryOut]:
    # Raw ownership check — intentionally allows deleted diaries so their entries can be restored
    result = await db.execute(
        select(Diary).where(Diary.id == diary_id, Diary.owner_user_id == user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="not_found")
    entry_result = await db.execute(
        select(Entry)
        .options(
            selectinload(Entry.events),
            selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule),
            selectinload(Entry.llm_generations),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
            selectinload(Entry.enrichments),
        )
        .where(Entry.diary_id == diary_id, Entry.deleted_at.is_not(None))
        .order_by(Entry.deleted_at.desc())
    )
    return [_entry_out_from_orm(e, can_edit=True) for e in entry_result.scalars()]


@router.get("/diaries/{diary_id}/entries", response_model=list[EntryOut])
async def list_entries(
    diary_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    cursor: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EntryOut]:
    diary, role = await _get_diary_or_404(diary_id, user, db)

    q = (
        select(Entry)
        .options(
            selectinload(Entry.events),
            selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule),
            selectinload(Entry.llm_generations),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
            selectinload(Entry.enrichments),
        )
        .where(Entry.diary_id == diary_id, Entry.deleted_at.is_(None))
    )

    # Viewers only see published
    if role == "viewer":
        q = q.where(Entry.status == "published")
    elif status_filter:
        q = q.where(Entry.status == status_filter)

    if from_date:
        q = q.where(Entry.entry_date >= from_date)
    if to_date:
        q = q.where(Entry.entry_date <= to_date)

    if cursor is not None:
        # Keyset pagination: `cursor` is the id of the last entry on the previous
        # page; return the entries that sort after it.
        cursor_row = (
            await db.execute(
                select(Entry.entry_date, Entry.created_at, Entry.id).where(
                    Entry.id == cursor, Entry.diary_id == diary_id
                )
            )
        ).one_or_none()
        if cursor_row is None:
            raise HTTPException(status_code=400, detail="invalid_cursor")
        q = q.where(_after_cursor(cursor_row.entry_date, cursor_row.created_at, cursor_row.id))

    q = q.order_by(*ENTRY_TIMELINE_ORDER).limit(limit)

    result = await db.execute(q)
    return [_entry_out_from_orm(e, can_edit=role != "viewer") for e in result.scalars()]


@router.post(
    "/diaries/{diary_id}/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED
)
async def create_entry(
    diary_id: uuid.UUID,
    body: EntryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    await enforce_entry_tier_limit(
        owner_user_id=user.id,
        source="manual",
        db=db,
        owner_subscription_tier=user.subscription_tier,
    )

    entry = Entry(
        diary_id=diary_id,
        entry_date=body.entry_date,
        entry_end_date=body.entry_end_date,
        title=body.title,
        body_markdown=body.body_markdown,
        # Text supplied at creation is the person's own; an empty entry has
        # nothing a person wrote yet.
        body_source="user" if (body.body_markdown or "").strip() else "llm",
        status="draft",
        created_by="manual",
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry, ["events", "rule_matches", "llm_generations", "entry_photos", "enrichments"])
    return _entry_out_from_orm(entry, can_edit=True)


@router.get("/entries/{entry_id}", response_model=EntryOut)
async def get_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    entry, _, role = await _get_entry_or_404(entry_id, user, db)
    return _entry_out_from_orm(entry, can_edit=role != "viewer")


@router.patch("/entries/{entry_id}", response_model=EntryOut)
async def patch_entry(
    entry_id: uuid.UUID,
    body: EntryPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)

    updates = body.model_dump(exclude_unset=True)
    # Record that a person has changed the words. Automatic clean-up (a
    # cancelled or moved event) only ever removes text nobody has touched.
    text_changed = any(
        field in updates and updates[field] != getattr(entry, field)
        for field in ("title", "body_markdown")
    )
    if text_changed:
        entry.body_source = "user"
    for field, value in updates.items():
        setattr(entry, field, value)
    if text_changed:
        # A warning goes once its word has left the text. A pending suggestion stays.
        entry.flagged_tokens = still_present(entry.flagged_tokens or [], _entry_text(entry))
    if entry.entry_end_date is not None and entry.entry_end_date < entry.entry_date:
        raise HTTPException(status_code=422, detail="entry_end_date_before_entry_date")
    return _entry_out_from_orm(entry, can_edit=True)


@router.post("/entries/{entry_id}/publish", response_model=EntryOut)
async def publish_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    entry, diary, role = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    if entry.status == "published":
        return _entry_out_from_orm(entry, can_edit=True)

    # entry_edit_diffs (design/04-llm-integration.md) is not captured yet: the
    # original LLM body is not stored separately, so there is nothing to diff.
    entry.status = "published"
    entry.published_at = datetime.now(tz=UTC)
    # Publishing is the parent's sign-off: warnings and any pending suggestion go.
    entry.flagged_tokens = []
    _clear_suggestion(entry)
    return _entry_out_from_orm(entry, can_edit=True)


@router.post("/entries/{entry_id}/suggestion/accept", response_model=EntryOut)
async def accept_suggestion(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    """Replace the entry's title and text with the AI's pending suggestion."""
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    if entry.suggested_body_markdown is None:
        raise HTTPException(status_code=409, detail="no_suggestion")

    old_warnings = entry.flagged_tokens or []
    published = entry.status == "published"
    if entry.suggested_title is not None:
        entry.title = entry.suggested_title
    entry.body_markdown = entry.suggested_body_markdown
    entry.body_source = entry.suggested_body_source or "llm"
    # The suggestion's own warnings, plus earlier ones whose word is still there:
    # a blend treats the old text as the parent's, so it would not re-flag them.
    warnings = list(entry.suggested_flagged_tokens or [])
    warnings += [w for w in still_present(old_warnings, _entry_text(entry)) if w not in warnings]
    # Accepting into a published entry is the parent's sign-off, as publishing is.
    entry.flagged_tokens = [] if published else warnings
    _clear_suggestion(entry)
    return _entry_out_from_orm(entry, can_edit=True)


@router.delete("/entries/{entry_id}/suggestion", response_model=EntryOut)
async def dismiss_suggestion(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    """Discard the AI's pending suggestion; the entry's text is unchanged."""
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    if entry.suggested_body_markdown is None:
        raise HTTPException(status_code=409, detail="no_suggestion")
    _clear_suggestion(entry)
    return _entry_out_from_orm(entry, can_edit=True)


@router.post("/entries/{entry_id}/unpublish", response_model=EntryOut)
async def unpublish_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    entry.status = "draft"
    entry.published_at = None
    return _entry_out_from_orm(entry, can_edit=True)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    entry.deleted_at = datetime.now(tz=UTC)


@router.post("/entries/{entry_id}/restore", response_model=EntryOut)
async def restore_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    result = await db.execute(
        select(Entry)
        .options(
            selectinload(Entry.events),
            selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule),
            selectinload(Entry.llm_generations),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
            selectinload(Entry.enrichments),
        )
        .where(Entry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="not_found")
    diary, role = await _get_diary_or_404(entry.diary_id, user, db, require_owner=False)
    owner_result = await db.execute(select(User).where(User.id == diary.owner_user_id))
    owner = owner_result.scalar_one()
    await enforce_entry_tier_limit(
        owner_user_id=owner.id,
        source=entry.created_by,
        db=db,
        owner_subscription_tier=owner.subscription_tier,
    )
    entry.deleted_at = None
    return _entry_out_from_orm(entry, can_edit=role != "viewer")


@router.post("/entries/{entry_id}/regenerate", response_model=EntryOut)
async def regenerate_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntryOut:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    from app.workers.tasks import generate_entry_draft

    # Nothing about the entry changes here, so there is no commit to wait for.
    generate_entry_draft.delay(str(entry.id), trigger="regenerate")
    return _entry_out_from_orm(entry, can_edit=True)
