from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import deletion_state, get_current_user
from app.core.database import get_db
from app.models import AuditLog, Diary, DiaryPermission, ScanJob, User
from app.services.diary_slugs import unique_slug
from app.services.plan_view import OwnerPlanOut, owner_plan_out
from app.services.read_only import ensure_diary_writable
from app.services.sharing import effective_role, people_counts, person_name
from app.services.tier import limits_for, tier_limit_error

router = APIRouter(prefix="/diaries", tags=["diaries"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:80] or "diary"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def _valid_timezone(v: str) -> str:
    # Workers resolve this with pytz on every event; an unknown name would fail
    # every scan for the diary, so reject it at the door.
    if v not in pytz.all_timezones_set:
        raise ValueError("unknown IANA timezone")
    return v


SubjectRelation = Literal["self", "child", "family", "other_person"]
VoiceOverride = Literal["first_singular", "first_plural", "second", "third"]
Timezone = Annotated[str, AfterValidator(_valid_timezone)]
DiaryName = Annotated[str, Field(min_length=1, max_length=200)]
# Beat dispatches due scans every 5 minutes, so anything lower buys nothing.
ScanIntervalMinutes = Annotated[int, Field(ge=5, le=10080)]


class DiaryCreate(BaseModel):
    name: DiaryName
    timezone: Timezone
    subject_name: Annotated[str, Field(max_length=200)] | None = None
    subject_relation: SubjectRelation = "self"
    voice_override: VoiceOverride | None = None
    tone_hint: Annotated[str, Field(min_length=1, max_length=500)] = "warm, narrative"
    scan_interval_minutes: ScanIntervalMinutes = 60


class DiaryPatch(BaseModel):
    name: DiaryName | None = None
    slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)] | None = None
    subject_name: Annotated[str, Field(max_length=200)] | None = None
    subject_relation: SubjectRelation | None = None
    voice_override: VoiceOverride | None = None
    tone_hint: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    timezone: Timezone | None = None
    scan_interval_minutes: ScanIntervalMinutes | None = None
    scan_enabled: bool | None = None
    notifications_muted: bool | None = None
    lat: Annotated[float | None, Field(ge=-90, le=90)] = None
    lon: Annotated[float | None, Field(ge=-180, le=180)] = None
    # The home place's name, from the city search (item 24).
    location_name: Annotated[str, Field(max_length=200)] | None = None


class PeopleOut(BaseModel):
    used: int  # members plus pending invitations
    limit: int | None  # the owner's plan limit; None means unlimited


class DeletionNoticeOut(BaseModel):
    owner_name: str
    deletes_on: datetime
    becomes_yours: bool  # the caller is the person the owner chose to receive the diary
    goes_to_name: str | None = None  # who the owner chose to receive it, if anyone


class _DiaryFields(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    subject_name: str | None
    subject_relation: str
    voice_override: str | None
    tone_hint: str
    scan_enabled: bool
    scan_interval_minutes: int
    lat: float | None = None
    lon: float | None = None
    location_name: str | None = None
    deleted_at: datetime | None
    hard_delete_after: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DiaryOut(_DiaryFields):
    plan: OwnerPlanOut | None = None  # the owner's plan; set on GET /diaries/{id} only
    # No defaults: a response built without them must fail, not claim ownership.
    role: Literal["owner", "editor", "viewer"]  # the caller's effective role
    owner_name: str
    people: PeopleOut | None = None  # owner only
    deletion_notice: DeletionNoticeOut | None = None  # members only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_diary_or_404(
    diary_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    require_owner: bool = False,
) -> tuple[Diary, str | None]:
    """Return (diary, role) or raise 404. role is None for owner, else the effective role."""
    result = await db.execute(select(Diary).where(Diary.id == diary_id, Diary.deleted_at.is_(None)))
    diary = result.scalar_one_or_none()
    if diary is None:
        raise HTTPException(status_code=404, detail="not_found")

    if diary.owner_user_id == user.id:
        if require_owner:
            return diary, None
        return diary, None

    # Check permissions
    perm_result = await db.execute(
        select(DiaryPermission).where(
            DiaryPermission.diary_id == diary_id,
            DiaryPermission.user_id == user.id,
        )
    )
    perm = perm_result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=404, detail="not_found")
    if require_owner:
        raise HTTPException(status_code=403, detail="forbidden")
    # An editor whose owner is off the Family plan acts as a viewer; the stored role is kept.
    owner_tier = (
        await db.execute(select(User.subscription_tier).where(User.id == diary.owner_user_id))
    ).scalar_one_or_none()
    return diary, effective_role(perm.role, owner_tier or "free")


def _diary_out(
    diary: Diary,
    *,
    role: Literal["owner", "editor", "viewer"],
    owner: User,
    caller: User,
    people: PeopleOut | None = None,
    goes_to: User | None = None,
) -> DiaryOut:
    """Serialize a diary for `caller`. Home coordinates go to the owner only (TR-20),
    like photo and entry coordinates (S-12); co-parents get the place name.

    `role` is the caller's effective role. People counts go to the owner, the
    deletion notice to members.
    """
    fields = _DiaryFields.model_validate(diary).model_dump()
    is_owner = role == "owner"
    if not is_owner:
        fields["lat"] = None
        fields["lon"] = None
    out = DiaryOut(
        **fields, role=role, owner_name=person_name(owner), people=people if is_owner else None
    )
    if not is_owner and deletion_state(owner) == "pending":
        deadline = owner.hard_delete_after
        if deadline is None:
            raise RuntimeError("pending deletion without a deadline")
        out.deletion_notice = DeletionNoticeOut(
            owner_name=out.owner_name,
            deletes_on=deadline,
            becomes_yours=diary.handover_to_user_id == caller.id,
            goes_to_name=person_name(goes_to) if goes_to is not None else None,
        )
    return out


async def _diaries_out(
    db: AsyncSession, diaries: list[Diary], user: User, roles: dict[uuid.UUID, str] | None = None
) -> list[DiaryOut]:
    """Serialize diaries for `user` with owners and people counts loaded in bulk.

    `roles` holds the role for each diary the user does not own.
    """
    owner_ids = {d.owner_user_id for d in diaries}
    owners = {
        u.id: u for u in (await db.execute(select(User).where(User.id.in_(owner_ids)))).scalars()
    }
    counts = await people_counts(db, [d.id for d in diaries if d.owner_user_id == user.id])
    recipient_ids = {d.handover_to_user_id for d in diaries if d.handover_to_user_id is not None}
    recipients = (
        {
            u.id: u
            for u in (await db.execute(select(User).where(User.id.in_(recipient_ids)))).scalars()
        }
        if recipient_ids
        else {}
    )
    result = []
    for d in diaries:
        owner = owners[d.owner_user_id]
        if d.owner_user_id == user.id:
            limit = limits_for(owner.subscription_tier).people_per_diary
            result.append(
                _diary_out(
                    d,
                    role="owner",
                    owner=owner,
                    caller=user,
                    people=PeopleOut(used=counts[d.id], limit=limit),
                )
            )
        else:
            role = effective_role((roles or {}).get(d.id, "viewer"), owner.subscription_tier)
            result.append(
                _diary_out(
                    d,
                    role=role,
                    owner=owner,
                    caller=user,
                    goes_to=recipients.get(d.handover_to_user_id)
                    if d.handover_to_user_id
                    else None,
                )
            )
    return result


# Columns a PATCH may set to NULL. An explicit null for any other field is
# ignored, because those columns are NOT NULL.
_CLEARABLE_DIARY_FIELDS = frozenset(
    {"lat", "lon", "location_name", "voice_override", "subject_name"}
)
_HOME_PLACE_FIELDS = ("lat", "lon", "location_name")


def _diary_updates(body: DiaryPatch) -> dict:
    """Fields to write for a PATCH: everything sent, minus nulls for required columns."""
    return {
        field: value
        for field, value in body.model_dump(exclude_unset=True).items()
        if value is not None or field in _CLEARABLE_DIARY_FIELDS
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DiaryOut])
async def list_diaries(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[DiaryOut]:
    owned = await db.execute(
        select(Diary)
        .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
        .order_by(func.lower(Diary.name), Diary.id)
    )
    shared = await db.execute(
        select(Diary, DiaryPermission.role)
        .join(DiaryPermission, DiaryPermission.diary_id == Diary.id)
        .where(DiaryPermission.user_id == user.id, Diary.deleted_at.is_(None))
        .order_by(func.lower(Diary.name), Diary.id)
    )
    shared_rows = shared.all()
    roles = {d.id: role for d, role in shared_rows}
    diaries = [*owned.scalars(), *(d for d, _ in shared_rows)]
    return await _diaries_out(db, diaries, user, roles)


@router.post("", response_model=DiaryOut, status_code=status.HTTP_201_CREATED)
async def create_diary(
    body: DiaryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> DiaryOut:
    limit = limits_for(user.subscription_tier).diaries

    # Advisory lock to prevent check-then-create race. The key is derived in
    # Postgres: Python's hash() of a str is salted per process, so two API
    # workers would compute different keys and never contend.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:uid))").bindparams(uid=str(user.id))
    )
    count_result = await db.execute(
        select(func.count())
        .select_from(Diary)
        .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
    )
    current = count_result.scalar_one()
    if current >= limit:
        raise tier_limit_error(source="diary", limit=limit, current=current, needed=current + 1)

    slug = await unique_slug(db, user.id, _slugify(body.name))

    diary = Diary(
        owner_user_id=user.id,
        name=body.name,
        slug=slug,
        timezone=body.timezone,
        subject_name=body.subject_name,
        subject_relation=body.subject_relation,
        voice_override=body.voice_override,
        tone_hint=body.tone_hint,
        scan_interval_minutes=body.scan_interval_minutes,
    )
    db.add(diary)
    await db.flush()

    # Create 1:1 scan job
    db.add(ScanJob(diary_id=diary.id))
    await db.refresh(diary)
    # Commit before returning so the row is visible to concurrent requests that
    # receive this response and immediately issue follow-up calls (e.g. DELETE).
    await db.commit()
    return (await _diaries_out(db, [diary], user))[0]


@router.get("/trash", response_model=list[DiaryOut])
async def list_deleted_diaries(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[DiaryOut]:
    result = await db.execute(
        select(Diary)
        .where(
            Diary.owner_user_id == user.id,
            Diary.deleted_at.is_not(None),
            Diary.hard_delete_after > datetime.now(tz=UTC),
        )
        .order_by(Diary.hard_delete_after.asc())
    )
    return await _diaries_out(db, list(result.scalars()), user)


@router.get("/{diary_id}", response_model=DiaryOut)
async def get_diary(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> DiaryOut:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    out = (await _diaries_out(db, [diary], user, {diary.id: role} if role else None))[0]
    # The owner's numbers go to the owner and editors only, never to viewers.
    out.plan = None if role == "viewer" else await owner_plan_out(db, diary)
    return out


@router.patch("/{diary_id}", response_model=DiaryOut)
async def patch_diary(
    diary_id: uuid.UUID,
    body: DiaryPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> DiaryOut:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    await ensure_diary_writable(db, diary)

    home_before = tuple(getattr(diary, f) for f in _HOME_PLACE_FIELDS)
    for field, value in _diary_updates(body).items():
        setattr(diary, field, value)

    # A home place has both coordinates or neither; without them it has no name.
    if (diary.lat is None) != (diary.lon is None):
        raise HTTPException(status_code=422, detail="incomplete_location")
    if diary.lat is None:
        diary.location_name = None

    await db.flush()
    await db.refresh(diary)
    if tuple(getattr(diary, f) for f in _HOME_PLACE_FIELDS) != home_before:
        # Entries without a place of their own follow the home place (item 24).
        await db.commit()  # so the worker sees it; sessions keep objects loaded on commit
        from app.workers.tasks import refresh_diary_locations

        try:
            refresh_diary_locations.delay(str(diary.id))
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "refresh_diary_locations_queue_failed diary=%s", diary.id
            )
    return (await _diaries_out(db, [diary], user))[0]


@router.delete("/{diary_id}", response_model=DiaryOut)
async def delete_diary(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> DiaryOut:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    now = datetime.now(tz=UTC)
    diary.deleted_at = now
    diary.hard_delete_after = now + timedelta(days=30)
    diary.scan_enabled = False
    db.add(
        AuditLog(user_id=user.id, action="diary.delete", target_type="diary", target_id=diary.id)
    )
    return (await _diaries_out(db, [diary], user))[0]


@router.post("/{diary_id}/restore", response_model=DiaryOut)
async def restore_diary(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> DiaryOut:
    # Fetch even soft-deleted diaries for restore
    result = await db.execute(
        select(Diary).where(Diary.id == diary_id, Diary.owner_user_id == user.id)
    )
    diary = result.scalar_one_or_none()
    if diary is None:
        raise HTTPException(status_code=404, detail="not_found")
    if diary.hard_delete_after and diary.hard_delete_after < datetime.now(tz=UTC):
        raise HTTPException(status_code=410, detail="grace_period_expired")
    # Check tier limit before restoring
    limit = limits_for(user.subscription_tier).diaries
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:uid))").bindparams(uid=str(user.id))
    )
    count_result = await db.execute(
        select(func.count())
        .select_from(Diary)
        .where(Diary.owner_user_id == user.id, Diary.deleted_at.is_(None))
    )
    current = count_result.scalar_one()
    if current >= limit:
        raise tier_limit_error(source="diary", limit=limit, current=current, needed=current + 1)
    # If the slug is now taken by another of the owner's diaries, assign a new unique one
    diary.slug = await unique_slug(db, user.id, diary.slug, exclude_diary_id=diary_id)
    diary.deleted_at = None
    diary.hard_delete_after = None
    diary.scan_enabled = True
    return (await _diaries_out(db, [diary], user))[0]
