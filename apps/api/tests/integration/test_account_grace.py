"""A deleted account can log in and restore during its 7-day grace (C-14).

Pending (deleted_at and hard_delete_after set, deadline in the future): login and
Google sign-in work; /me, refresh, logout and restore work; everything else is 403
account_pending_deletion. Blocked (admin CLI: no hard_delete_after; or past the
deadline): 401 account_unavailable everywhere, restore included.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SocialIdentity, User
from tests.integration.google_helpers import connect_cookie_for
from tests.integration.photo_helpers import PASSWORD, register_and_login
from tests.integration.test_google_connect_security import (
    _authorize,
    _callback,
    _query,
    _token_count,
)

ME = "/v1/auth/me"
LOGIN = "/v1/auth/login"
ACCOUNT = "/v1/auth/account"
RESTORE = "/v1/auth/account/restore"


def _detail(r: httpx.Response) -> str:
    return r.json()["error"]["message"]


async def _login(client: AsyncClient, email: str) -> httpx.Response:
    return await client.post(LOGIN, json={"email": email, "password": PASSWORD})


async def _bearer(client: AsyncClient, email: str) -> dict[str, str]:
    r = await _login(client, email)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _set_deletion(
    db: AsyncSession, email: str, *, deleted_at: datetime | None, hard_delete_after: datetime | None
) -> None:
    await db.execute(
        update(User)
        .where(User.email == email)
        .values(deleted_at=deleted_at, hard_delete_after=hard_delete_after)
    )
    await db.commit()


# ---------------------------------------------------------------------------
# deletion_state: the one shared definition
# ---------------------------------------------------------------------------


def _user(deleted_at: datetime | None, hard_delete_after: datetime | None) -> User:
    return User(email="x@example.com", deleted_at=deleted_at, hard_delete_after=hard_delete_after)


def test_deletion_state_boundaries():
    from app.core.auth import deletion_state

    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert deletion_state(_user(None, None), now) == "active"
    assert deletion_state(_user(now, now + timedelta(seconds=1)), now) == "pending"
    # Exactly at the deadline the hourly job may already have run: blocked.
    assert deletion_state(_user(now, now), now) == "blocked"
    assert deletion_state(_user(now, now - timedelta(seconds=1)), now) == "blocked"
    # Admin CLI: deleted_at with no deadline.
    assert deletion_state(_user(now, None), now) == "blocked"
    # Malformed: a deadline without deleted_at is never treated as active.
    assert deletion_state(_user(None, now + timedelta(days=1)), now) == "blocked"


# ---------------------------------------------------------------------------
# Pending
# ---------------------------------------------------------------------------


async def test_login_during_grace_succeeds(client: AsyncClient):
    auth = await register_and_login(client, "grace-login@example.com")
    assert (await client.delete(ACCOUNT, headers=auth)).status_code == 200

    r = await _login(client, "grace-login@example.com")

    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


async def test_pending_account_gets_403_elsewhere(client: AsyncClient):
    auth = await register_and_login(client, "grace-403@example.com")
    await client.delete(ACCOUNT, headers=auth)
    fresh = await _bearer(client, "grace-403@example.com")

    for headers in (auth, fresh):
        for method, url in (
            ("GET", "/v1/diaries"),
            ("GET", "/v1/auth/me/plan"),
            ("POST", "/v1/auth/logout-all"),
            ("DELETE", ACCOUNT),
            ("GET", "/v1/integrations/google/authorize?scopes=calendar"),
        ):
            r = await client.request(method, url, headers=headers)
            assert r.status_code == 403, (method, url, r.text)
            assert _detail(r) == "account_pending_deletion"


async def test_pending_account_may_use_me_refresh_logout(client: AsyncClient):
    auth = await register_and_login(client, "grace-me@example.com")
    deleted = await client.delete(ACCOUNT, headers=auth)
    deadline = datetime.fromisoformat(deleted.json()["deletion_scheduled_for"])

    login = await _login(client, "grace-me@example.com")
    cookie = login.cookies["refresh_token"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = await client.get(ME, headers=headers)
    assert me.status_code == 200, me.text
    assert datetime.fromisoformat(me.json()["deletion_scheduled_for"]) == deadline

    refreshed = await client.post("/v1/auth/refresh", headers={"Cookie": f"refresh_token={cookie}"})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]

    out = await client.post(
        "/v1/auth/logout",
        headers={"Cookie": f"refresh_token={refreshed.cookies['refresh_token']}"},
    )
    assert out.status_code == 204


async def test_restore_reenables_everything(client: AsyncClient):
    auth = await register_and_login(client, "grace-restore@example.com")
    await client.delete(ACCOUNT, headers=auth)
    headers = await _bearer(client, "grace-restore@example.com")

    r = await client.post(RESTORE, headers=headers)

    assert r.status_code == 204, r.text
    assert (await client.get("/v1/diaries", headers=headers)).status_code == 200
    me = await client.get(ME, headers=headers)
    assert me.status_code == 200
    assert me.json()["deletion_scheduled_for"] is None


async def test_delete_account_returns_deadline(client: AsyncClient):
    auth = await register_and_login(client, "grace-deadline@example.com")

    r = await client.delete(ACCOUNT, headers=auth)

    assert r.status_code == 200, r.text
    when = datetime.fromisoformat(r.json()["deletion_scheduled_for"])
    assert when.tzinfo is not None
    expected = datetime.now(tz=UTC) + timedelta(days=7)
    assert abs((when - expected).total_seconds()) < 60


# ---------------------------------------------------------------------------
# Blocked
# ---------------------------------------------------------------------------


async def test_after_grace_login_and_me_are_401(client: AsyncClient, db_session: AsyncSession):
    email = "grace-past@example.com"
    auth = await register_and_login(client, email)
    await client.delete(ACCOUNT, headers=auth)
    login = await _login(client, email)
    cookie = login.cookies["refresh_token"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    now = datetime.now(tz=UTC)
    await _set_deletion(
        db_session,
        email,
        deleted_at=now - timedelta(days=7),
        hard_delete_after=now - timedelta(seconds=1),
    )

    r = await _login(client, email)
    assert r.status_code == 401
    assert _detail(r) == "account_unavailable"
    for method, url in (("GET", ME), ("POST", RESTORE), ("GET", "/v1/diaries")):
        r = await client.request(method, url, headers=headers)
        assert r.status_code == 401, (method, url, r.text)
        assert _detail(r) == "account_unavailable"
    r = await client.post("/v1/auth/refresh", headers={"Cookie": f"refresh_token={cookie}"})
    assert r.status_code == 401
    assert _detail(r) == "account_unavailable"


async def test_admin_blocked_account_stays_401(client: AsyncClient, db_session: AsyncSession):
    email = "grace-admin@example.com"
    auth = await register_and_login(client, email)
    await _set_deletion(db_session, email, deleted_at=datetime.now(tz=UTC), hard_delete_after=None)

    r = await _login(client, email)
    assert r.status_code == 401
    assert _detail(r) == "account_unavailable"
    for method, url in (("GET", ME), ("POST", RESTORE), ("GET", "/v1/diaries")):
        r = await client.request(method, url, headers=auth)
        assert r.status_code == 401, (method, url, r.text)
        assert _detail(r) == "account_unavailable"

    db_session.expire_all()
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    assert user.deleted_at is not None  # restore did not undo the admin block


# ---------------------------------------------------------------------------
# Google sign-in and the Google connect callback
# ---------------------------------------------------------------------------


async def test_google_login_follows_the_same_rules(client: AsyncClient, db_session: AsyncSession):
    email = "grace-google@example.com"
    auth = await register_and_login(client, email)
    uid = (await db_session.execute(select(User.id).where(User.email == email))).scalar_one()
    db_session.add(SocialIdentity(user_id=uid, provider="google", provider_user_id="g-123"))
    await db_session.commit()
    await client.delete(ACCOUNT, headers=auth)

    id_info = {"sub": "g-123", "email": email, "email_verified": True}
    with patch("app.routers.v1.auth.google_id_token.verify_oauth2_token", return_value=id_info):
        pending = await client.post("/v1/auth/social/google", json={"id_token": "t"})
        assert pending.status_code == 200, pending.text
        assert pending.json()["access_token"]

        now = datetime.now(tz=UTC)
        await _set_deletion(
            db_session, email, deleted_at=now, hard_delete_after=now - timedelta(seconds=1)
        )
        past = await client.post("/v1/auth/social/google", json={"id_token": "t"})
        assert past.status_code == 401
        assert _detail(past) == "account_unavailable"

        await _set_deletion(db_session, email, deleted_at=now, hard_delete_after=None)
        admin = await client.post("/v1/auth/social/google", json={"id_token": "t"})
        assert admin.status_code == 401
        assert _detail(admin) == "account_unavailable"


async def test_oauth_callback_refuses_pending_account(
    client: AsyncClient, db_session: AsyncSession
):
    """A connect started before deleting must not store tokens after (Review Focus 2)."""
    email = "grace-callback@example.com"
    auth = await register_and_login(client, email)
    state, _ = await _authorize(client, auth)
    assert (await client.delete(ACCOUNT, headers=auth)).status_code == 200

    exchange = AsyncMock(return_value=httpx.Response(200, json={}))
    with patch("httpx.AsyncClient.post", exchange):
        r = await client.get(
            "/v1/integrations/google/callback",
            params={"code": "c", "state": state},
            headers=connect_cookie_for(state),
            follow_redirects=False,
        )

    assert r.status_code in (302, 307)
    assert _query(r) == {"google": "failed", "reason": "account_unavailable"}
    assert exchange.await_count == 0  # the code is never exchanged
    assert await _token_count(db_session, email) == 0


async def test_oauth_callback_refuses_blocked_account(
    client: AsyncClient, db_session: AsyncSession
):
    email = "grace-callback-blocked@example.com"
    auth = await register_and_login(client, email)
    state, _ = await _authorize(client, auth)
    await _set_deletion(db_session, email, deleted_at=datetime.now(tz=UTC), hard_delete_after=None)

    r = await _callback(client, state, headers=connect_cookie_for(state))

    assert _query(r) == {"google": "failed", "reason": "account_unavailable"}
    assert await _token_count(db_session, email) == 0


async def test_oauth_callback_still_connects_active_account(
    client: AsyncClient, db_session: AsyncSession
):
    email = "grace-callback-ok@example.com"
    auth = await register_and_login(client, email)
    state, _ = await _authorize(client, auth)

    r = await _callback(client, state, headers=connect_cookie_for(state))

    assert _query(r) == {"google": "connected"}
    assert await _token_count(db_session, email) == 1


# ---------------------------------------------------------------------------
# Row 54: restore and the hourly hard delete serialize on the user row
# ---------------------------------------------------------------------------


async def test_hard_delete_stops_if_restored_after_pickup(
    client: AsyncClient, db_session: AsyncSession
):
    """The hourly job picked the account, then a restore landed: nothing is deleted."""
    import uuid

    from app.models import Diary
    from app.workers.hard_delete import hard_delete_user

    email = "grace-race-restored@example.com"
    auth = await register_and_login(client, email)
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    user_id = (await db_session.execute(select(User.id).where(User.email == email))).scalar_one()
    # As a restore leaves it: both columns cleared.
    await _set_deletion(db_session, email, deleted_at=None, hard_delete_after=None)

    await hard_delete_user(user_id)

    db_session.expire_all()
    assert (await db_session.execute(select(User.id).where(User.id == user_id))).first()
    assert (
        await db_session.execute(select(Diary.id).where(Diary.id == uuid.UUID(diary["id"])))
    ).first()


async def test_hard_delete_stops_if_deadline_not_reached(
    client: AsyncClient, db_session: AsyncSession
):
    from app.workers.hard_delete import hard_delete_user

    email = "grace-race-early@example.com"
    auth = await register_and_login(client, email)
    await client.delete(ACCOUNT, headers=auth)  # pending, deadline in 7 days
    user_id = (await db_session.execute(select(User.id).where(User.email == email))).scalar_one()

    await hard_delete_user(user_id)

    db_session.expire_all()
    assert (await db_session.execute(select(User.id).where(User.id == user_id))).first()


async def test_restore_refuses_when_row_no_longer_pending(
    client: AsyncClient, db_session: AsyncSession
):
    """The request passed the grace check, then the deadline passed before the row lock."""
    from app.core.auth import get_current_user_within_grace

    email = "grace-race-lost@example.com"
    auth = await register_and_login(client, email)
    await client.delete(ACCOUNT, headers=auth)
    headers = await _bearer(client, email)
    stale = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    now = datetime.now(tz=UTC)
    await _set_deletion(
        db_session, email, deleted_at=now - timedelta(days=7), hard_delete_after=now - timedelta(seconds=1)
    )

    app = client._transport.app  # type: ignore[attr-defined]

    async def passed_grace_check() -> User:
        return stale

    app.dependency_overrides[get_current_user_within_grace] = passed_grace_check
    try:
        r = await client.post(RESTORE, headers=headers)
    finally:
        app.dependency_overrides.pop(get_current_user_within_grace, None)

    assert r.status_code == 401, r.text
    assert _detail(r) == "account_unavailable"
    db_session.expire_all()
    row = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    assert row.deleted_at is not None and row.hard_delete_after is not None
