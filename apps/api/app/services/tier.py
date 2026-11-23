"""Plan limits and the 403 raised when a request would exceed them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException

LimitSource = Literal[
    "diary", "ai_draft", "photo", "diary_read_only", "backfill", "people", "editor"
]


@dataclass(frozen=True)
class PlanLimits:
    diaries: int
    ai_drafts_per_month: int | None  # None = unlimited
    photos_per_entry: int | None  # None = unlimited
    backfill: bool
    scan_past_days: int | None  # how far back a scan or rule apply may look; None = unlimited
    people_per_diary: int | None  # members plus pending invites, per diary; None = unlimited
    editors: bool  # may the owner give someone the editor role


# Ordered cheapest first: required_tier() returns the first plan that fits.
PLAN_LIMITS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        diaries=1,
        ai_drafts_per_month=5,
        photos_per_entry=1,
        backfill=False,
        scan_past_days=90,
        people_per_diary=3,
        editors=False,
    ),
    "plus": PlanLimits(
        diaries=2,
        ai_drafts_per_month=None,
        photos_per_entry=None,
        backfill=True,
        scan_past_days=None,
        people_per_diary=10,
        editors=False,
    ),
    "family": PlanLimits(
        diaries=4,
        ai_drafts_per_month=None,
        photos_per_entry=None,
        backfill=True,
        scan_past_days=None,
        people_per_diary=None,
        editors=True,
    ),
}

BACKFILL_LIMIT_NOTE = "Backfill is on Plus and Family. Free scans look back 90 days."

PLAN_NAMES: dict[str, str] = {"free": "Free", "plus": "Plus", "family": "Family"}


def limits_for(tier: str) -> PlanLimits:
    """The limits for *tier*; an unknown tier is treated as free."""
    return PLAN_LIMITS.get(tier, PLAN_LIMITS["free"])


def _limit_for_source(limits: PlanLimits, source: LimitSource) -> int | None:
    if source in ("diary", "diary_read_only"):
        return limits.diaries
    if source == "ai_draft":
        return limits.ai_drafts_per_month
    if source == "backfill":
        return limits.scan_past_days
    if source == "people":
        return limits.people_per_diary
    if source == "editor":
        return None if limits.editors else 0
    return limits.photos_per_entry


def required_tier(source: LimitSource, needed: int) -> str:
    """The cheapest plan whose limit for *source* allows *needed* (family if none does)."""
    for tier, limits in PLAN_LIMITS.items():
        cap = _limit_for_source(limits, source)
        if cap is None or cap >= needed:
            return tier
    return "family"


def tier_limit_error(
    *,
    source: LimitSource,
    limit: int,
    current: int,
    needed: int,
    resets_on: date | None = None,
) -> HTTPException:
    details: dict[str, Any] = {
        "limit": limit,
        "current": current,
        "source": source,
        "required_tier": required_tier(source, needed),
    }
    if resets_on is not None:
        details["resets_on"] = resets_on.isoformat()
    return HTTPException(status_code=403, detail={"code": "tier_limit", "details": details})


def ensure_backfill_allowed(tier: str, past_days: int | None) -> None:
    """Raise a tier_limit 403 unless *tier* may look back *past_days*.

    ``past_days=None`` is a date-range backfill, which needs the backfill feature.
    """
    limits = limits_for(tier)
    if past_days is None:
        if limits.backfill:
            return
        # Needed is one past the cap, so required_tier() points at the first plan without one.
        cap = limits.scan_past_days
        raise tier_limit_error(source="backfill", limit=cap or 0, current=0, needed=(cap or 0) + 1)
    cap = limits.scan_past_days
    if cap is not None and past_days > cap:
        raise tier_limit_error(source="backfill", limit=cap, current=past_days, needed=past_days)
