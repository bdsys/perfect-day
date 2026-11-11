"""Unit tests: require_reauth is an async dependency that checks the Redis re-auth marker."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.auth import require_reauth


def _request(session_id: str = "sess-1") -> SimpleNamespace:
    return SimpleNamespace(headers={"X-Session-ID": session_id})


@pytest.mark.asyncio
async def test_passes_when_reauth_marker_exists():
    user = SimpleNamespace(id=uuid.uuid4())
    redis = AsyncMock()
    redis.exists.return_value = 1
    with patch("app.core.dependencies.get_redis", return_value=redis):
        await require_reauth(_request(), user)  # must not raise, must not touch the event loop
    redis.exists.assert_awaited_once_with(f"reauth:{user.id}:sess-1")


@pytest.mark.asyncio
async def test_rejects_when_marker_missing():
    user = SimpleNamespace(id=uuid.uuid4())
    redis = AsyncMock()
    redis.exists.return_value = 0
    with (
        patch("app.core.dependencies.get_redis", return_value=redis),
        pytest.raises(HTTPException) as exc,
    ):
        await require_reauth(_request(), user)
    assert exc.value.status_code == 403
    assert exc.value.detail == "reauth_required"
