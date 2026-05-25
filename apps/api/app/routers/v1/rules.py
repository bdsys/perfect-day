"""Endpoints for managing auto-creation rules: CRUD, preview, and apply."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import AutoCreationRule, Event, User
from app.routers.v1.diaries import _get_diary_or_404

log = structlog.get_logger()

router = APIRouter(tags=["rules"])

# ---------------------------------------------------------------------------
# Condition tree constants
# ---------------------------------------------------------------------------

_VALID_FIELDS = {"title", "description", "location", "attendee_email"}
_VALID_LEAF_OPS = {"contains", "equals", "not_contains"}
_VALID_GROUP_OPS = {"AND", "OR"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RuleOptions(BaseModel):
    recurring: str = "per_instance"  # "per_instance" | "per_series"
    multi_day: str = "per_day"  # "per_day" | "spanning"


class RuleCreate(BaseModel):
    name: str
    condition: dict  # validated as condition tree
    options: RuleOptions = Field(default_factory=RuleOptions)
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    condition: dict | None = None
    options: RuleOptions | None = None
    enabled: bool | None = None


class RuleOut(BaseModel):
    id: uuid.UUID
    diary_id: uuid.UUID
    name: str
    condition: dict
    options: dict
    enabled: bool
    last_applied_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PreviewBody(BaseModel):
    condition: dict
    options: RuleOptions = Field(default_factory=RuleOptions)


class PreviewOut(BaseModel):
    matched_count: int
    total_evaluated: int
    threshold_exceeded: bool  # True if matched_count > 30
    sample: list[dict]  # up to 10 matched event summaries


class ApplyBody(BaseModel):
    days: int = Field(ge=1, le=365)


# ---------------------------------------------------------------------------
# Condition tree validator
# ---------------------------------------------------------------------------


def _validate_condition(
    condition: dict, _depth: int = 0, _leaf_count: list[int] | None = None
) -> None:
    """Recursively validate a condition tree. Raises ValueError on any problem."""
    if _leaf_count is None:
        _leaf_count = [0]
    if _depth >= 5:
        raise ValueError("condition tree exceeds maximum depth of 5")
    op = condition.get("op")
    if op in _VALID_GROUP_OPS:
        children = condition.get("children") or []
        if len(children) > 20:
            raise ValueError("group has more than 20 children")
        for child in children:
            _validate_condition(child, _depth + 1, _leaf_count)
    else:
        # Leaf node
        _leaf_count[0] += 1
        if _leaf_count[0] > 50:
            raise ValueError("condition tree exceeds 50 leaves")
        if condition.get("field") not in _VALID_FIELDS:
            raise ValueError(f"unknown field: {condition.get('field')!r}")
        if condition.get("op") not in _VALID_LEAF_OPS:
            raise ValueError(f"unknown op: {condition.get('op')!r}")
        if not condition.get("value"):
            raise ValueError("leaf value must not be empty")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_rule_or_404(
    rule_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> tuple[AutoCreationRule, str | None]:
    """Load rule, verify diary access, return (rule, role). role is None for owner."""
    result = await db.execute(
        select(AutoCreationRule).where(AutoCreationRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Verify the requesting user has access to the diary this rule belongs to.
    _, role = await _get_diary_or_404(rule.diary_id, user, db)
    return rule, role


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/diaries/{diary_id}/rules",
    response_model=list[RuleOut],
)
async def list_rules(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RuleOut]:
    """List all auto-creation rules for a diary."""
    await _get_diary_or_404(diary_id, user, db)

    result = await db.execute(
        select(AutoCreationRule)
        .where(AutoCreationRule.diary_id == diary_id)
        .order_by(AutoCreationRule.created_at)
    )
    return [RuleOut.model_validate(r) for r in result.scalars()]


@router.post(
    "/diaries/{diary_id}/rules",
    response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    diary_id: uuid.UUID,
    body: RuleCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    """Create a new auto-creation rule."""
    _, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        _validate_condition(body.condition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rule = AutoCreationRule(
        diary_id=diary_id,
        name=body.name,
        condition=body.condition,
        options=body.options.model_dump(),
        enabled=body.enabled,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return RuleOut.model_validate(rule)


@router.get(
    "/rules/{rule_id}",
    response_model=RuleOut,
)
async def get_rule(
    rule_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    """Retrieve a single rule by ID."""
    rule, _ = await _get_rule_or_404(rule_id, user, db)
    return RuleOut.model_validate(rule)


@router.patch(
    "/rules/{rule_id}",
    response_model=RuleOut,
)
async def patch_rule(
    rule_id: uuid.UUID,
    body: RulePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RuleOut:
    """Partially update a rule."""
    rule, role = await _get_rule_or_404(rule_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    if body.condition is not None:
        try:
            _validate_condition(body.condition)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.name is not None:
        rule.name = body.name
    if body.condition is not None:
        rule.condition = body.condition
    if body.options is not None:
        rule.options = body.options.model_dump()
    if body.enabled is not None:
        rule.enabled = body.enabled

    await db.commit()
    await db.refresh(rule)
    return RuleOut.model_validate(rule)


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_rule(
    rule_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Hard-delete a rule (cascade handles EntryRuleMatch / RuleSeriesClaim)."""
    rule, role = await _get_rule_or_404(rule_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    await db.delete(rule)
    await db.commit()


@router.post(
    "/diaries/{diary_id}/rules/preview",
    response_model=PreviewOut,
)
async def preview_rule(
    diary_id: uuid.UUID,
    body: PreviewBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewOut:
    """Preview how many events a condition would match over the last 90 days."""
    await _get_diary_or_404(diary_id, user, db)

    try:
        _validate_condition(body.condition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cutoff = datetime.now(tz=UTC) - timedelta(days=90)
    result = await db.execute(
        select(Event)
        .where(Event.diary_id == diary_id)
        .where(Event.occurred_at >= cutoff)
        .order_by(Event.occurred_at.desc())
        .limit(5000)
    )
    events = list(result.scalars())

    from app.workers.rules import match_event  # noqa: PLC0415

    matched: list[Event] = []
    for event in events:
        if match_event(body.condition, event.payload or {}):
            matched.append(event)

    sample = [
        {
            "summary": (event.payload or {}).get("summary", ""),
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "location": (event.payload or {}).get("location", ""),
        }
        for event in matched[:10]
    ]

    return PreviewOut(
        matched_count=len(matched),
        total_evaluated=len(events),
        threshold_exceeded=len(matched) > 30,
        sample=sample,
    )


@router.post(
    "/rules/{rule_id}/apply",
    response_model=dict,
)
async def apply_rule(
    rule_id: uuid.UUID,
    body: ApplyBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Queue a backfill job to apply a rule over the past N days."""
    rule, role = await _get_rule_or_404(rule_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        from app.workers.tasks import apply_rule_backfill  # noqa: PLC0415

        apply_rule_backfill.delay(str(rule.id), body.days)
    except Exception:
        log.exception(
            "Failed to queue apply_rule_backfill for rule %s", rule.id
        )

    return {"queued": True}
