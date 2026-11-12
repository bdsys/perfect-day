"""A lost or refused Google Calendar connection is reported, not silent.

C-18: a scan that cannot read the calendar says so.
C-19: refusing the calendar permission gets its own message and does not
replace a working connection.
Findings in design/code-review-2026-11.md.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, unquote_plus, urlparse

import httpx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OAuthToken, ScanRun
from tests.fixtures.factories import make_diary, make_user

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"


# ---------------------------------------------------------------------------
# C-18 — the scan reports a calendar it could not read
# ---------------------------------------------------------------------------


async def _diary_with_google(db: AsyncSession, **token_fields):
    owner = await make_user(db)
    diary = await make_diary(db, owner=owner)
    fields = {"scopes_granted": ["calendar.readonly"], **token_fields}
    db.add(
        OAuthToken(
            user_id=owner.id,
            provider="google",
            access_token_ciphertext=b"not-a-real-ciphertext",
            **fields,
        )
    )
    await db.commit()
    return diary


async def _scan(diary, *, refresh=None, sync=None) -> ScanRun:
    from app.workers.tasks import _scan_diary

    refresh = refresh or AsyncMock(return_value="tok")
    sync = sync or AsyncMock(return_value=0)
    with (
        patch("app.workers.token_refresh.ensure_fresh_access_token", refresh),
        patch("app.workers.calendar_sync.sync_calendar", sync),
    ):
        await _scan_diary(str(diary.id))

    from app.workers.utils import db_session

    async with db_session() as db:
        return (await db.execute(select(ScanRun).where(ScanRun.diary_id == diary.id))).scalar_one()


def _codes(run: ScanRun) -> list[str]:
    return [e["error_class"] for e in run.errors or []]


class TestScanReportsTheCalendar:
    async def test_a_revoked_connection_is_reported_as_disconnected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session, revoked_at=datetime.now(tz=UTC))

        run = await _scan(diary)

        assert run.status == "partial"
        assert _codes(run) == ["calendar_disconnected"]
        assert "Reconnect" in run.errors[0]["message"]

    async def test_a_connection_without_calendar_permission_is_reported_as_disconnected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session, scopes_granted=[])

        run = await _scan(diary)

        assert run.status == "partial"
        assert _codes(run) == ["calendar_disconnected"]

    async def test_google_revoking_the_grant_during_the_scan_is_reported_as_disconnected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session)

        async def _grant_gone(oauth_token, db):
            oauth_token.revoked_at = datetime.now(tz=UTC)
            return None

        run = await _scan(diary, refresh=_grant_gone)

        assert run.status == "partial"
        assert _codes(run) == ["calendar_disconnected"]

    async def test_a_refresh_that_fails_for_now_is_reported_as_unavailable(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session)

        run = await _scan(diary, refresh=AsyncMock(return_value=None))

        assert run.status == "partial"
        assert _codes(run) == ["calendar_unavailable"]
        assert "next scan" in run.errors[0]["message"]

    async def test_a_diary_whose_owner_never_connected_google_scans_quietly(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await make_user(db_session)
        diary = await make_diary(db_session, owner=owner)

        run = await _scan(diary)

        assert run.status == "success"
        assert run.errors is None

    async def test_a_calendar_error_shows_a_plain_message_and_keeps_the_detail(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        diary = await _diary_with_google(db_session)
        boom = AsyncMock(side_effect=RuntimeError("GET https://internal/secret-path 500"))

        run = await _scan(diary, sync=boom)

        assert run.status == "partial"
        error = run.errors[0]
        assert "secret-path" not in error["message"]
        assert "secret-path" in error["detail"]


class TestScanRunsShowTheirErrors:
    async def test_the_runs_list_returns_each_errors_message_but_not_its_detail(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        r = await client.post(
            "/v1/auth/register", json={"email": "runs-errors@example.com", "password": "Password1!"}
        )
        auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()
        now = datetime.now(tz=UTC)
        db_session.add(
            ScanRun(
                diary_id=uuid.UUID(diary["id"]),
                triggered_by="manual",
                started_at=now,
                completed_at=now,
                status="partial",
                errors=[
                    {
                        "source": "google_calendar",
                        "error_class": "calendar_disconnected",
                        "message": "Google Calendar is disconnected.",
                        "detail": "internal detail",
                    }
                ],
            )
        )
        await db_session.commit()

        r = await client.get(f"/v1/diaries/{diary['id']}/scan/runs", headers=auth)

        assert r.status_code == 200
        assert r.json()[0]["errors"] == [
            {"source": "google_calendar", "message": "Google Calendar is disconnected."}
        ]


# ---------------------------------------------------------------------------
# C-19 — what the Google callback does with each answer
# ---------------------------------------------------------------------------


def _id_token() -> str:
    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    payload = json.dumps({"email": "parent@gmail.com", "name": "Parent", "sub": "s"}).encode()
    return f"{b64url(b'{}')}.{b64url(payload)}.sig"


async def _register(client: AsyncClient, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _state(client: AsyncClient, auth: dict) -> str:
    r = await client.get("/v1/integrations/google/authorize?scopes=calendar", headers=auth)
    query = parse_qs(urlparse(r.json()["url"]).query)
    return query["state"][0]


async def _callback(
    client: AsyncClient, state: str, *, granted: str, access_token: str = "ya29.new"  # noqa: S107
):
    """Google returns to the callback having granted the *granted* scope string."""
    token_response = {
        "access_token": access_token,
        "refresh_token": "1//refresh",
        "expires_in": 3600,
        "scope": granted,
        "id_token": _id_token(),
    }
    with patch(
        "httpx.AsyncClient.post", AsyncMock(return_value=httpx.Response(200, json=token_response))
    ):
        return await client.get(
            "/v1/integrations/google/callback",
            params={"code": "c", "state": state, "scope": granted},
            follow_redirects=False,
        )


def _redirect_query(r) -> dict[str, str]:
    assert r.status_code in (302, 307), r.text
    query = urlparse(unquote_plus(r.headers["location"])).query
    return {k: v[0] for k, v in parse_qs(query).items()}


async def _stored_token(db: AsyncSession, email: str) -> OAuthToken | None:
    from app.models import User

    db.expire_all()
    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return (
        await db.execute(select(OAuthToken).where(OAuthToken.user_id == user_id))
    ).scalar_one_or_none()


class TestCallbackAnswers:
    async def test_calendar_granted_connects(self, client: AsyncClient, db_session: AsyncSession):
        auth = await _register(client, "cb-ok@example.com")

        r = await _callback(client, await _state(client, auth), granted=f"openid {SCOPE_CALENDAR}")

        assert _redirect_query(r) == {"google": "connected"}
        token = await _stored_token(db_session, "cb-ok@example.com")
        assert token.scopes_granted == ["calendar.readonly"]

    async def test_calendar_not_ticked_says_so_and_saves_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth = await _register(client, "cb-noticks@example.com")

        r = await _callback(client, await _state(client, auth), granted="openid email profile")

        assert _redirect_query(r) == {"google": "partial", "missing": "calendar"}
        assert await _stored_token(db_session, "cb-noticks@example.com") is None

    async def test_calendar_not_ticked_leaves_a_working_connection_alone(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        email = "cb-keep@example.com"
        auth = await _register(client, email)
        await _callback(client, await _state(client, auth), granted=SCOPE_CALENDAR)
        before = await _stored_token(db_session, email)
        before_ciphertext = before.access_token_ciphertext

        r = await _callback(
            client,
            await _state(client, auth),
            granted="openid",
            access_token="ya29.no-calendar",  # noqa: S106 — a fake token
        )

        assert _redirect_query(r)["missing"] == "calendar"
        after = await _stored_token(db_session, email)
        assert after.scopes_granted == ["calendar.readonly"]
        assert after.access_token_ciphertext == before_ciphertext
        assert after.revoked_at is None

    async def test_calendar_not_ticked_leaves_a_lost_connection_lost(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        email = "cb-lost@example.com"
        auth = await _register(client, email)
        await _callback(client, await _state(client, auth), granted=SCOPE_CALENDAR)
        token = await _stored_token(db_session, email)
        token.revoked_at = datetime.now(tz=UTC) - timedelta(days=1)
        await db_session.commit()

        await _callback(client, await _state(client, auth), granted="openid")

        assert (await _stored_token(db_session, email)).revoked_at is not None

    async def test_pressing_cancel_on_google_says_cancelled(self, client: AsyncClient):
        auth = await _register(client, "cb-cancel@example.com")

        r = await client.get(
            "/v1/integrations/google/callback",
            params={"error": "access_denied", "state": await _state(client, auth)},
            follow_redirects=False,
        )

        assert _redirect_query(r) == {"google": "cancelled"}

    async def test_a_failed_token_exchange_says_failed(self, client: AsyncClient):
        auth = await _register(client, "cb-fail@example.com")
        state = await _state(client, auth)

        with patch(
            "httpx.AsyncClient.post",
            AsyncMock(return_value=httpx.Response(400, json={"error": "invalid_grant"})),
        ):
            r = await client.get(
                "/v1/integrations/google/callback",
                params={"code": "c", "state": state},
                follow_redirects=False,
            )

        assert _redirect_query(r) == {"google": "failed", "reason": "token_exchange_failed"}

    async def test_reusing_a_connect_link_says_failed(self, client: AsyncClient):
        auth = await _register(client, "cb-reuse@example.com")
        state = await _state(client, auth)
        await _callback(client, state, granted=SCOPE_CALENDAR)

        r = await _callback(client, state, granted=SCOPE_CALENDAR)

        assert _redirect_query(r) == {"google": "failed", "reason": "nonce_reuse"}


# ---------------------------------------------------------------------------
# The diary page asks about the OWNER's connection: scans read the owner's
# calendar, and only the owner may scan (decision 2026-11-12).
# ---------------------------------------------------------------------------


async def _owner_and_diary(client: AsyncClient, email: str) -> tuple[dict, dict]:
    auth = await _register(client, email)
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return auth, diary


async def _give_google(db: AsyncSession, email: str, **fields) -> None:
    from app.models import User

    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    db.add(
        OAuthToken(
            user_id=user_id,
            provider="google",
            access_token_ciphertext=b"x",
            **{"scopes_granted": ["calendar.readonly"], **fields},
        )
    )
    await db.commit()


async def _scan_config(client: AsyncClient, diary: dict, auth: dict) -> dict:
    r = await client.get(f"/v1/diaries/{diary['id']}/scan", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


class TestScanConfigReportsTheOwnersCalendar:
    async def test_owner_who_never_connected(self, client: AsyncClient):
        auth, diary = await _owner_and_diary(client, "sc-none@example.com")

        config = await _scan_config(client, diary, auth)

        assert config["calendar"] == "none"
        assert config["can_scan"] is True

    async def test_owner_with_a_working_connection(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth, diary = await _owner_and_diary(client, "sc-ok@example.com")
        await _give_google(db_session, "sc-ok@example.com")

        assert (await _scan_config(client, diary, auth))["calendar"] == "connected"

    async def test_owner_with_a_revoked_connection(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth, diary = await _owner_and_diary(client, "sc-revoked@example.com")
        await _give_google(db_session, "sc-revoked@example.com", revoked_at=datetime.now(tz=UTC))

        assert (await _scan_config(client, diary, auth))["calendar"] == "lost"

    async def test_owner_without_calendar_permission(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth, diary = await _owner_and_diary(client, "sc-noscope@example.com")
        await _give_google(db_session, "sc-noscope@example.com", scopes_granted=[])

        assert (await _scan_config(client, diary, auth))["calendar"] == "lost"

    async def test_co_parent_sees_the_owners_connection_and_cannot_scan(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.models import DiaryPermission, User

        _, diary = await _owner_and_diary(client, "sc-owner@example.com")
        await _give_google(db_session, "sc-owner@example.com", revoked_at=datetime.now(tz=UTC))
        editor_auth = await _register(client, "sc-editor@example.com")
        # The co-parent's own working connection must not count for this diary.
        await _give_google(db_session, "sc-editor@example.com")
        editor_id = (
            await db_session.execute(select(User.id).where(User.email == "sc-editor@example.com"))
        ).scalar_one()
        db_session.add(
            DiaryPermission(diary_id=uuid.UUID(diary["id"]), user_id=editor_id, role="editor")
        )
        await db_session.commit()

        config = await _scan_config(client, diary, editor_auth)

        assert config["calendar"] == "lost"
        assert config["can_scan"] is False
