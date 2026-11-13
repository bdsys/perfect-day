"""Google connect is tied to the starting browser (S-5); disconnect revokes at Google (S-8)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_oauth_token
from app.models import OAuthToken, User
from tests.integration.google_helpers import CONNECT_COOKIE, connect_cookie_for

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"


def _id_token(email: str) -> str:
    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    return f"{b64url(b'{}')}.{b64url(json.dumps({'email': email, 'name': 'P'}).encode())}.sig"


async def _register(client: AsyncClient, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _authorize(client: AsyncClient, auth: dict) -> tuple[str, httpx.Response]:
    r = await client.get("/v1/integrations/google/authorize?scopes=calendar", headers=auth)
    assert r.status_code == 200, r.text
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0], r


async def _callback(client: AsyncClient, state: str, headers: dict | None = None):
    token_response = {
        "access_token": "ya29.a",
        "refresh_token": "1//r",
        "expires_in": 3600,
        "scope": SCOPE_CALENDAR,
        "id_token": _id_token("p@gmail.com"),
    }
    with patch(
        "httpx.AsyncClient.post", AsyncMock(return_value=httpx.Response(200, json=token_response))
    ):
        return await client.get(
            "/v1/integrations/google/callback",
            params={"code": "c", "state": state, "scope": SCOPE_CALENDAR},
            headers=headers or {},
            follow_redirects=False,
        )


async def _token_count(db: AsyncSession, email: str) -> int:
    db.expire_all()
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    rows = (await db.execute(select(OAuthToken).where(OAuthToken.user_id == uid))).scalars().all()
    return len(rows)


def _query(r) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}


async def test_callback_without_cookie_saves_nothing(client: AsyncClient, db_session: AsyncSession):
    auth = await _register(client, "sec-nocookie@example.com")
    state, _ = await _authorize(client, auth)

    r = await _callback(client, state)

    assert _query(r) == {"google": "failed", "reason": "browser_mismatch"}
    assert await _token_count(db_session, "sec-nocookie@example.com") == 0


async def test_callback_with_other_browsers_cookie_saves_nothing(
    client: AsyncClient, db_session: AsyncSession
):
    victim = await _register(client, "sec-victim@example.com")
    attacker = await _register(client, "sec-attacker@example.com")
    victim_state, _ = await _authorize(client, victim)
    attacker_state, _ = await _authorize(client, attacker)

    # The victim's browser holds a cookie from a different authorize.
    r = await _callback(client, attacker_state, headers=connect_cookie_for(victim_state))

    assert _query(r) == {"google": "failed", "reason": "browser_mismatch"}
    assert await _token_count(db_session, "sec-attacker@example.com") == 0

    # The refused attempt did not burn the legitimate user's nonce.
    ok = await _callback(client, attacker_state, headers=connect_cookie_for(attacker_state))
    assert _query(ok) == {"google": "connected"}


async def test_callback_with_matching_cookie_saves(client: AsyncClient, db_session: AsyncSession):
    auth = await _register(client, "sec-ok@example.com")
    state, _ = await _authorize(client, auth)

    r = await _callback(client, state, headers=connect_cookie_for(state))

    assert _query(r) == {"google": "connected"}
    assert await _token_count(db_session, "sec-ok@example.com") == 1


async def test_authorize_sets_the_cookie_the_callback_expects(client: AsyncClient):
    auth = await _register(client, "sec-set@example.com")
    state, resp = await _authorize(client, auth)

    header = resp.headers["set-cookie"]
    expected = connect_cookie_for(state)["Cookie"]
    assert header.startswith(expected)
    low = header.lower()
    assert "httponly" in low and "secure" in low and "samesite=lax" in low
    assert "path=/v1/integrations/google/callback" in low
    assert "max-age=600" in low


async def test_cookie_cleared_after_callback(client: AsyncClient):
    auth = await _register(client, "sec-clear@example.com")

    def cleared(r) -> bool:
        h = r.headers.get("set-cookie", "").lower()
        return (
            h.startswith(f"{CONNECT_COOKIE}=")
            and "max-age=0" in h
            and "path=/v1/integrations/google/callback" in h
        )

    state, _ = await _authorize(client, auth)
    assert cleared(await _callback(client, state, headers=connect_cookie_for(state)))  # success
    assert cleared(await _callback(client, state))  # refused (no cookie)
    state2, _ = await _authorize(client, auth)
    r = await client.get(
        "/v1/integrations/google/callback",
        params={"error": "access_denied", "state": state2},
        follow_redirects=False,
    )
    assert cleared(r)  # cancelled
    r = await client.get(
        "/v1/integrations/google/callback", params={"error": "boom"}, follow_redirects=False
    )
    assert cleared(r)  # failed


async def _seed_token(db: AsyncSession, email: str, refresh: str | None = "1//refresh-secret"):
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    db.add(
        OAuthToken(
            user_id=uid,
            provider="google",
            access_token_ciphertext=encrypt_oauth_token("ya29.access-secret"),
            refresh_token_ciphertext=encrypt_oauth_token(refresh) if refresh else None,
            scopes_granted=["calendar.readonly"],
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            google_email="p@gmail.com",
        )
    )
    await db.commit()


async def test_disconnect_revokes_at_google_and_deletes_tokens(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "sec-revoke@example.com")
    await _seed_token(db_session, "sec-revoke@example.com")
    post = AsyncMock(return_value=httpx.Response(200))

    with patch("httpx.AsyncClient.post", post):
        r = await client.delete("/v1/integrations/google", headers=auth)

    assert r.status_code == 204
    assert post.await_count == 1
    assert post.await_args.args[0] == "https://oauth2.googleapis.com/revoke"
    assert post.await_args.kwargs["data"] == {"token": "1//refresh-secret"}
    assert await _token_count(db_session, "sec-revoke@example.com") == 0
    assert (await client.get("/v1/integrations", headers=auth)).json() == []


async def test_disconnect_revokes_access_token_when_no_refresh_token(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "sec-noref@example.com")
    await _seed_token(db_session, "sec-noref@example.com", refresh=None)
    post = AsyncMock(return_value=httpx.Response(200))

    with patch("httpx.AsyncClient.post", post):
        await client.delete("/v1/integrations/google", headers=auth)

    assert post.await_args.kwargs["data"] == {"token": "ya29.access-secret"}


async def test_disconnect_deletes_tokens_even_if_google_down(
    client: AsyncClient, db_session: AsyncSession, caplog
):
    auth = await _register(client, "sec-down@example.com")

    for failure in (
        AsyncMock(side_effect=httpx.ConnectTimeout("slow")),
        AsyncMock(return_value=httpx.Response(503)),
    ):
        await _seed_token(db_session, "sec-down@example.com")
        with patch("httpx.AsyncClient.post", failure):
            r = await client.delete("/v1/integrations/google", headers=auth)
        assert r.status_code == 204
        assert await _token_count(db_session, "sec-down@example.com") == 0

    assert "refresh-secret" not in caplog.text
    assert "access-secret" not in caplog.text
