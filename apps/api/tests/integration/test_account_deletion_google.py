"""Deleting the account revokes the grant at Google and forgets the tokens (TR-33)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_google_connect_security import (
    _register,
    _seed_token,
    _token_count,
)


async def test_account_deletion_revokes_at_google_and_forgets_tokens(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "del-google@example.com")
    await _seed_token(db_session, "del-google@example.com")
    post = AsyncMock(return_value=httpx.Response(200))

    with patch("httpx.AsyncClient.post", post):
        r = await client.delete("/v1/auth/account", headers=auth)

    assert r.status_code == 204
    assert post.await_count == 1
    assert post.await_args.args[0] == "https://oauth2.googleapis.com/revoke"
    assert post.await_args.kwargs["data"] == {"token": "1//refresh-secret"}
    assert await _token_count(db_session, "del-google@example.com") == 0


async def test_account_deletion_forgets_tokens_even_if_google_down(
    client: AsyncClient, db_session: AsyncSession, caplog
):
    auth = await _register(client, "del-down@example.com")
    await _seed_token(db_session, "del-down@example.com")

    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=httpx.ConnectTimeout("slow"))):
        r = await client.delete("/v1/auth/account", headers=auth)

    assert r.status_code == 204
    assert await _token_count(db_session, "del-down@example.com") == 0
    assert "refresh-secret" not in caplog.text
