"""Integration test: concurrent refreshes with one cookie must not fork a token family (TR-29)."""

from __future__ import annotations

import asyncio

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token


async def test_simultaneous_refreshes_leave_one_live_token(
    client: AsyncClient, db_session: AsyncSession
):
    r = await client.post(
        "/v1/auth/register", json={"email": "race@example.com", "password": "Password1!"}
    )
    assert r.status_code == 201, r.text
    cookie = r.cookies["refresh_token"]
    client.cookies.clear()

    # The cookie is Secure, so the http:// client never sends it itself; pass it by header.
    headers = {"Cookie": f"refresh_token={cookie}"}
    first, second = await asyncio.gather(
        client.post("/v1/auth/refresh", headers=headers),
        client.post("/v1/auth/refresh", headers=headers),
    )
    for resp in (first, second):
        assert resp.status_code == 200, resp.text
        assert "access_token" in resp.json()

    family_id = (
        await db_session.execute(
            text("SELECT family_id FROM refresh_tokens WHERE token_hash = :h"),
            {"h": hash_token(cookie)},
        )
    ).scalar_one()
    live = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE family_id = :f AND revoked_at IS NULL"),
            {"f": family_id},
        )
    ).scalar_one()
    assert live == 1

    # Only the request that rotated sets a new cookie; the grace-path one does not.
    rotated = [x for x in (first, second) if "refresh_token" in x.cookies]
    assert len(rotated) == 1
    client.cookies.clear()
    third = await client.post(
        "/v1/auth/refresh",
        headers={"Cookie": f"refresh_token={rotated[0].cookies['refresh_token']}"},
    )
    assert third.status_code == 200, third.text
