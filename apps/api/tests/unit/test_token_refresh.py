"""Unit tests: a failed Google token refresh only revokes the integration if the grant is gone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.workers.token_refresh import ensure_fresh_access_token


def _expired_token() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="user-1",
        provider="google",
        revoked_at=None,
        expires_at=datetime.now(tz=UTC) - timedelta(minutes=5),
        access_token_ciphertext=b"old-access",
        refresh_token_ciphertext=b"refresh",
    )


async def _refresh_with(response: httpx.Response, token: SimpleNamespace) -> str | None:
    redis = AsyncMock()
    redis.set.return_value = True

    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.core.dependencies.get_redis", return_value=redis),
        patch("app.workers.token_refresh.httpx.AsyncClient", return_value=client),
        patch("app.workers.token_refresh.decrypt_oauth_token", return_value="plain"),
        patch("app.workers.token_refresh.encrypt_oauth_token", side_effect=lambda v: v.encode()),
    ):
        result = await ensure_fresh_access_token(token, db=AsyncMock())
    redis.delete.assert_awaited_once()
    return result


def _resp(status: int, body: dict | None = None, text: str | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://oauth2.googleapis.com/token")
    if text is not None:
        return httpx.Response(status, text=text, request=request)
    return httpx.Response(status, json=body or {}, request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _resp(503, text="upstream unavailable"),
        _resp(429, {"error": "rate_limit_exceeded"}),
        _resp(401, {"error": "invalid_client"}),
        _resp(500, {"error": "internal_failure"}),
    ],
)
async def test_transient_or_config_failure_does_not_revoke(response: httpx.Response):
    token = _expired_token()
    assert await _refresh_with(response, token) is None
    assert token.revoked_at is None


@pytest.mark.asyncio
async def test_invalid_grant_revokes():
    token = _expired_token()
    assert await _refresh_with(_resp(400, {"error": "invalid_grant"}), token) is None
    assert token.revoked_at is not None


@pytest.mark.asyncio
async def test_successful_refresh_updates_token():
    token = _expired_token()
    result = await _refresh_with(_resp(200, {"access_token": "new", "expires_in": 3600}), token)
    assert result == "new"
    assert token.access_token_ciphertext == b"new"
    assert token.revoked_at is None
    assert token.expires_at > datetime.now(tz=UTC)
