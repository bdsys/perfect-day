"""Plan limits and the tier_limit error shape."""

from __future__ import annotations

from datetime import date

from app.services.tier import PLAN_NAMES, PlanLimits, limits_for, required_tier, tier_limit_error


def test_limits_per_plan() -> None:
    assert limits_for("free") == PlanLimits(1, 5, 1)
    assert limits_for("plus") == PlanLimits(2, None, None)
    assert limits_for("family") == PlanLimits(4, None, None)


def test_unknown_tier_is_free() -> None:
    assert limits_for("tier1") == limits_for("free")


def test_plan_names() -> None:
    assert PLAN_NAMES == {"free": "Free", "plus": "Plus", "family": "Family"}


def test_required_tier() -> None:
    assert required_tier("diary", 2) == "plus"
    assert required_tier("diary", 3) == "family"
    assert required_tier("diary", 9) == "family"
    assert required_tier("diary_read_only", 2) == "plus"
    assert required_tier("ai_draft", 6) == "plus"
    assert required_tier("photo", 2) == "plus"


def test_tier_limit_error_shape() -> None:
    exc = tier_limit_error(
        source="ai_draft", limit=5, current=5, needed=6, resets_on=date(2026, 12, 1)
    )
    assert exc.status_code == 403
    assert exc.detail == {
        "code": "tier_limit",
        "details": {
            "limit": 5,
            "current": 5,
            "source": "ai_draft",
            "required_tier": "plus",
            "resets_on": "2026-12-01",
        },
    }


def test_tier_limit_error_without_reset() -> None:
    exc = tier_limit_error(source="diary", limit=1, current=1, needed=2)
    assert "resets_on" not in exc.detail["details"]
