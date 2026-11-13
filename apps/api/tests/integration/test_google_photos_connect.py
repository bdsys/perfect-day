"""Connecting Google Photos (Picker API) must never disturb Calendar. Item 14."""

from __future__ import annotations

import base64
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, unquote_plus, urlparse

import httpx
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OAuthToken, User

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_PICKER = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
SCOPE_LIBRARY = "https://www.googleapis.com/auth/photoslibrary.readonly"
PICKER = "photospicker.mediaitems.readonly"


def _id_token(email: str) -> str:
    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    payload = json.dumps({"email": email, "name": "P", "sub": "s"}).encode()
    return f"{b64url(b'{}')}.{b64url(payload)}.sig"


async def _register(client: AsyncClient, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _authorize(client: AsyncClient, auth: dict, query: str) -> dict[str, str]:
    r = await client.get(f"/v1/integrations/google/authorize?{query}", headers=auth)
    assert r.status_code == 200, r.text
    return {k: v[0] for k, v in parse_qs(urlparse(r.json()["url"]).query).items()}


async def _callback(
    client: AsyncClient,
    state: str,
    *,
    granted: str,
    email: str | None = "p@gmail.com",
    access_token: str = "ya29.new",  # noqa: S107
):
    token_response = {
        "access_token": access_token,
        "refresh_token": "1//refresh",
        "expires_in": 3600,
        "scope": granted,
        "id_token": _id_token(email) if email else None,
    }
    with patch(
        "httpx.AsyncClient.post", AsyncMock(return_value=httpx.Response(200, json=token_response))
    ):
        return await client.get(
            "/v1/integrations/google/callback",
            params={"code": "c", "state": state, "scope": granted},
            follow_redirects=False,
        )


def _location(r) -> tuple[str, dict[str, str]]:
    assert r.status_code in (302, 307), r.text
    u = urlparse(unquote_plus(r.headers["location"]))
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


async def _token(db: AsyncSession, email: str) -> OAuthToken | None:
    db.expire_all()
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return (
        await db.execute(select(OAuthToken).where(OAuthToken.user_id == uid))
    ).scalar_one_or_none()


async def _connect_calendar(client, auth, email="p@gmail.com"):
    q = await _authorize(client, auth, "scopes=calendar")
    await _callback(client, q["state"], granted=f"openid {SCOPE_CALENDAR}", email=email)


async def test_authorize_requests_picker_scope_not_library(client: AsyncClient):
    auth = await _register(client, "ph-auth@example.com")

    q = await _authorize(client, auth, "scopes=photos")

    assert SCOPE_PICKER in q["scope"].split()
    assert "photoslibrary" not in q["scope"]


async def test_return_to_rides_through_and_bad_values_fall_back(client: AsyncClient):
    auth = await _register(client, "ph-ret@example.com")

    q = await _authorize(client, auth, "scopes=photos&return_to=/entries/abc?x=1")
    r = await _callback(client, q["state"], granted=SCOPE_PICKER)
    path, query = _location(r)
    assert (path, query["google"], query["x"]) == ("/entries/abc", "connected", "1")

    for bad in ("//evil.com", "https://evil.com", "/a\\b", "evil"):
        q = await _authorize(client, auth, f"scopes=photos&return_to={bad}")
        r = await _callback(client, q["state"], granted=SCOPE_PICKER)
        assert _location(r)[0] == "/diaries", bad


async def test_photos_only_connect_saves_for_user_without_calendar(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "ph-only@example.com")
    q = await _authorize(client, auth, "scopes=photos")

    r = await _callback(client, q["state"], granted=f"openid {SCOPE_PICKER}")

    assert _location(r)[1] == {"google": "connected"}
    token = await _token(db_session, "ph-only@example.com")
    assert token.scopes_granted == [PICKER]
    assert token.google_email == "p@gmail.com"
    listed = (await client.get("/v1/integrations", headers=auth)).json()
    assert listed[0]["photos"] is True


async def test_adding_photos_keeps_calendar(client: AsyncClient, db_session: AsyncSession):
    auth = await _register(client, "ph-add@example.com")
    await _connect_calendar(client, auth)
    before = (await _token(db_session, "ph-add@example.com")).access_token_ciphertext

    q = await _authorize(client, auth, "scopes=photos")
    assert q["login_hint"] == "p@gmail.com"
    r = await _callback(client, q["state"], granted=SCOPE_PICKER, access_token="ya29.second")  # noqa: S106

    assert _location(r)[1] == {"google": "connected"}
    token = await _token(db_session, "ph-add@example.com")
    assert set(token.scopes_granted) == {"calendar.readonly", PICKER}
    assert token.access_token_ciphertext != before
    from app.services.calendar_connection import calendar_state

    assert calendar_state(token) == "connected"


async def test_different_account_refused_and_calendar_untouched(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "ph-wrong@example.com")
    await _connect_calendar(client, auth)
    before = await _token(db_session, "ph-wrong@example.com")
    snap = (
        before.access_token_ciphertext,
        before.refresh_token_ciphertext,
        list(before.scopes_granted),
        before.google_email,
    )

    q = await _authorize(client, auth, "scopes=photos")
    r = await _callback(client, q["state"], granted=SCOPE_PICKER, email="other@gmail.com")

    _, query = _location(r)
    assert query == {"google": "wrong_account", "expected": "p@gmail.com"}
    after = await _token(db_session, "ph-wrong@example.com")
    assert (
        after.access_token_ciphertext,
        after.refresh_token_ciphertext,
        list(after.scopes_granted),
        after.google_email,
    ) == snap


async def test_photos_declined_changes_nothing(client: AsyncClient, db_session: AsyncSession):
    auth = await _register(client, "ph-decl@example.com")
    await _connect_calendar(client, auth)
    before = (await _token(db_session, "ph-decl@example.com")).access_token_ciphertext

    q = await _authorize(client, auth, "scopes=photos")
    r = await _callback(client, q["state"], granted="openid")

    assert _location(r)[1] == {"google": "partial", "missing": "photos"}
    token = await _token(db_session, "ph-decl@example.com")
    assert token.access_token_ciphertext == before
    assert token.scopes_granted == ["calendar.readonly"]

    # and for a user with no connection at all, nothing is created
    auth2 = await _register(client, "ph-decl2@example.com")
    q = await _authorize(client, auth2, "scopes=photos")
    await _callback(client, q["state"], granted="openid")
    assert await _token(db_session, "ph-decl2@example.com") is None


async def test_photos_only_token_does_not_say_calendar_lost():
    from app.services.calendar_connection import calendar_state

    tok = OAuthToken(scopes_granted=[PICKER], revoked_at=None)
    assert calendar_state(tok) == "none"
    tok.revoked_at = datetime.now(tz=UTC)
    assert calendar_state(tok) == "none"
    tok.scopes_granted = ["calendar.readonly"]
    assert calendar_state(tok) == "lost"


async def test_old_library_scope_removed_by_migration(
    client: AsyncClient, db_session: AsyncSession
):
    await _register(client, "ph-mig@example.com")
    uid = (
        await db_session.execute(select(User.id).where(User.email == "ph-mig@example.com"))
    ).scalar_one()
    db_session.add(
        OAuthToken(
            user_id=uid,
            provider="google",
            access_token_ciphertext=b"x",
            scopes_granted=["calendar.readonly", "photoslibrary.readonly"],
        )
    )
    await db_session.commit()
    path = Path(__file__).parents[2] / "alembic/versions/0014_photos_scope_name.py"
    spec = importlib.util.spec_from_file_location("m0014", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    await db_session.execute(text(mod.REMOVE_OLD_SCOPE_SQL))
    await db_session.commit()

    assert (await _token(db_session, "ph-mig@example.com")).scopes_granted == ["calendar.readonly"]


async def test_stored_null_email_accepts_and_records_account(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "ph-null@example.com")
    await _connect_calendar(client, auth)
    token = await _token(db_session, "ph-null@example.com")
    token.google_email = None
    token.google_name = None
    await db_session.commit()

    q = await _authorize(client, auth, "scopes=photos")
    assert "login_hint" not in q
    r = await _callback(client, q["state"], granted=SCOPE_PICKER, email="New@Gmail.com")

    assert _location(r)[1] == {"google": "connected"}
    token = await _token(db_session, "ph-null@example.com")
    assert token.google_email == "New@Gmail.com"
    assert token.google_name == "P"
    assert set(token.scopes_granted) == {"calendar.readonly", PICKER}


async def test_missing_consent_email_refused_when_stored_email_exists(
    client: AsyncClient, db_session: AsyncSession
):
    auth = await _register(client, "ph-noid@example.com")
    await _connect_calendar(client, auth)
    before = await _token(db_session, "ph-noid@example.com")
    snap = (before.access_token_ciphertext, list(before.scopes_granted), before.google_email)

    q = await _authorize(client, auth, "scopes=photos")
    r = await _callback(client, q["state"], granted=SCOPE_PICKER, email=None)

    assert _location(r)[1] == {"google": "wrong_account", "expected": "p@gmail.com"}
    after = await _token(db_session, "ph-noid@example.com")
    assert (after.access_token_ciphertext, list(after.scopes_granted), after.google_email) == snap


async def test_email_match_is_case_insensitive(client: AsyncClient, db_session: AsyncSession):
    auth = await _register(client, "ph-case@example.com")
    await _connect_calendar(client, auth)

    q = await _authorize(client, auth, "scopes=photos")
    r = await _callback(client, q["state"], granted=SCOPE_PICKER, email="P@GMAIL.com")

    assert _location(r)[1] == {"google": "connected"}
    token = await _token(db_session, "ph-case@example.com")
    assert set(token.scopes_granted) == {"calendar.readonly", PICKER}
