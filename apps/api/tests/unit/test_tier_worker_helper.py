"""Unit tests for try_enforce_entry_tier_limit — no-raise worker variant."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.tier import try_enforce_entry_tier_limit


@pytest.mark.asyncio
async def test_under_limit_returns_true():
    """When enforce_entry_tier_limit does not raise, returns (True, None)."""
    mock_db = AsyncMock()

    with patch("app.services.tier.enforce_entry_tier_limit", new_callable=AsyncMock) as mock_enforce:
        mock_enforce.return_value = None  # no exception = under limit

        ok, reason = await try_enforce_entry_tier_limit(
            user_id=uuid.uuid4(),
            diary_id=uuid.uuid4(),
            source="auto",
            db=mock_db,
            subscription_tier="free",
        )

    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_at_limit_returns_false():
    """When enforce_entry_tier_limit raises HTTPException, returns (False, detail)."""
    from fastapi import HTTPException
    mock_db = AsyncMock()

    with patch("app.services.tier.enforce_entry_tier_limit", new_callable=AsyncMock) as mock_enforce:
        mock_enforce.side_effect = HTTPException(status_code=402, detail="entry limit reached")

        ok, reason = await try_enforce_entry_tier_limit(
            user_id=uuid.uuid4(),
            diary_id=uuid.uuid4(),
            source="auto",
            db=mock_db,
            subscription_tier="free",
        )

    assert ok is False
    assert reason is not None
    assert "limit" in reason.lower()


@pytest.mark.asyncio
async def test_unlimited_tier_returns_true():
    """Paid tier — enforce passes without raising."""
    mock_db = AsyncMock()

    with patch("app.services.tier.enforce_entry_tier_limit", new_callable=AsyncMock) as mock_enforce:
        mock_enforce.return_value = None

        ok, reason = await try_enforce_entry_tier_limit(
            user_id=uuid.uuid4(),
            diary_id=uuid.uuid4(),
            source="auto",
            db=mock_db,
            subscription_tier="tier1",
        )

    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_db_error_propagates():
    """Non-HTTPException errors (e.g. DB errors) are NOT swallowed — they propagate."""
    from sqlalchemy.exc import OperationalError
    mock_db = AsyncMock()

    with patch("app.services.tier.enforce_entry_tier_limit", new_callable=AsyncMock) as mock_enforce:
        mock_enforce.side_effect = OperationalError("connection refused", params=None, orig=None)

        with pytest.raises(OperationalError):
            await try_enforce_entry_tier_limit(
                user_id=uuid.uuid4(),
                diary_id=uuid.uuid4(),
                source="auto",
                db=mock_db,
                subscription_tier="free",
            )
