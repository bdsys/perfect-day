from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Diary, Entry, LLMGeneration, User
from app.routers.v1.diaries import _get_diary_or_404

router = APIRouter(tags=["entries"])

TIER_ENTRY_LIMITS = {
    "free": {"manual": 5, "auto": 3},
    "tier1": {"manual": None, "auto": None},
    "tier2": {"manual": None, "auto": None},
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EntryCreate(BaseModel):
    entry_date: date
    entry_end_date: date | None = None
    title: str | None = None
    body_markdown: str | None = None


class EntryPatch(BaseModel):
    title: str | None = None
    body_markdown: str | None = None
    entry_date: date | None = None
    entry_end_date: date | None = None


class EntryOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    entry_date: date
    entry_end_date: date | None
    title: str | None
    body_markdown: str | None
    status: str
    created_by: str
    published_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_entry_or_404(
    entry_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    require_editor: bool = False,
) -> tuple[Entry, Diary, str | None]:
    result = await db.execute(select(Entry).where(Entry.id == entry_id, Entry.deleted_at.is_(None)))
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
) -> list[Entry]:
    diary, role = await _get_diary_or_404(diary_id, user, db)

    q = select(Entry).where(Entry.diary_id == diary_id, Entry.deleted_at.is_(None))

    # Viewers only see published
    if role == "viewer":
        q = q.where(Entry.status == "published")
    elif status_filter:
        q = q.where(Entry.status == status_filter)

    if from_date:
        q = q.where(Entry.entry_date >= from_date)
    if to_date:
        q = q.where(Entry.entry_date <= to_date)

    q = q.order_by(Entry.entry_date.desc(), Entry.created_at.desc()).limit(limit)

    result = await db.execute(q)
    return list(result.scalars())


@router.post(
    "/diaries/{diary_id}/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED
)
async def create_entry(
    diary_id: uuid.UUID,
    body: EntryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entry:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    entry = Entry(
        diary_id=diary_id,
        entry_date=body.entry_date,
        entry_end_date=body.entry_end_date,
        title=body.title,
        body_markdown=body.body_markdown,
        status="draft",
        created_by="manual",
    )
    db.add(entry)
    return entry


@router.get("/entries/{entry_id}", response_model=EntryOut)
async def get_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entry:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db)
    return entry


@router.patch("/entries/{entry_id}", response_model=EntryOut)
async def patch_entry(
    entry_id: uuid.UUID,
    body: EntryPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entry:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(entry, field, value)
    return entry


@router.post("/entries/{entry_id}/publish", response_model=EntryOut)
async def publish_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entry:
    entry, diary, role = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    if entry.status == "published":
        return entry

    # Capture edit diff if body was changed from last LLM output
    gen_result = await db.execute(
        select(LLMGeneration)
        .where(LLMGeneration.entry_id == entry_id, LLMGeneration.status == "success")
        .order_by(LLMGeneration.created_at.desc())
        .limit(1)
    )
    last_gen = gen_result.scalar_one_or_none()
    if last_gen is not None and entry.body_markdown:
        # We don't store the original LLM body separately, so diff is best-effort here.
        # The proper diff is captured by the UI passing original before editing.
        pass

    entry.status = "published"
    entry.published_at = datetime.now(tz=UTC)
    return entry


@router.post("/entries/{entry_id}/unpublish", response_model=EntryOut)
async def unpublish_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entry:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    entry.status = "draft"
    entry.published_at = None
    return entry


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
) -> Entry:
    result = await db.execute(select(Entry).where(Entry.id == entry_id))
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="not_found")
    await _get_diary_or_404(entry.diary_id, user, db, require_owner=False)
    entry.deleted_at = None
    return entry


@router.post("/entries/{entry_id}/regenerate", response_model=EntryOut)
async def regenerate_entry(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Entry:
    entry, _, _ = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    from app.workers.tasks import generate_entry_draft

    generate_entry_draft.delay(str(entry.id))
    return entry
