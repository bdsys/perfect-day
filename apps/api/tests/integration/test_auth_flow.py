"""Integration tests: full auth flow — register, login, refresh, logout, account lifecycle."""

from __future__ import annotations

from httpx import AsyncClient

REG_URL = "/v1/auth/register"
LOGIN_URL = "/v1/auth/login"
REFRESH_URL = "/v1/auth/refresh"
LOGOUT_URL = "/v1/auth/logout"
ME_URL = "/v1/auth/me"
ACCOUNT_URL = "/v1/auth/account"


async def _register(client: AsyncClient, email: str, password: str = "Password1!") -> dict:  # noqa: S107
    r = await client.post(REG_URL, json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


class TestRegister:
    async def test_register_returns_access_token(self, client):
        data = await _register(client, "reg@example.com")
        assert "access_token" in data

    async def test_duplicate_email_returns_409(self, client):
        await _register(client, "dup@example.com")
        r = await client.post(REG_URL, json={"email": "dup@example.com", "password": "Password1!"})
        assert r.status_code == 409

    async def test_short_password_rejected(self, client):
        r = await client.post(REG_URL, json={"email": "short@example.com", "password": "ab"})
        assert r.status_code == 422


class TestLogin:
    async def test_correct_credentials_return_token(self, client):
        await _register(client, "login@example.com")
        r = await client.post(
            LOGIN_URL, json={"email": "login@example.com", "password": "Password1!"}
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    async def test_wrong_password_returns_401(self, client):
        await _register(client, "wrong@example.com")
        r = await client.post(LOGIN_URL, json={"email": "wrong@example.com", "password": "bad"})
        assert r.status_code == 401

    async def test_unknown_email_returns_401(self, client):
        r = await client.post(LOGIN_URL, json={"email": "nobody@example.com", "password": "pw"})
        assert r.status_code == 401

    async def test_case_insensitive_email(self, client):
        await _register(client, "Case@example.com")
        r = await client.post(
            LOGIN_URL, json={"email": "CASE@EXAMPLE.COM", "password": "Password1!"}
        )
        assert r.status_code == 200


class TestMe:
    async def test_me_returns_user_info(self, client):
        tokens = await _register(client, "me@example.com")
        r = await client.get(ME_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert r.status_code == 200
        assert r.json()["email"] == "me@example.com"

    async def test_me_without_token_returns_401(self, client):
        r = await client.get(ME_URL)
        assert r.status_code == 401  # no credentials → 401

    async def test_me_with_garbage_token_returns_401(self, client):
        r = await client.get(ME_URL, headers={"Authorization": "Bearer garbage.token.here"})
        assert r.status_code == 401


class TestRefreshAndLogout:
    async def test_logout_invalidates_refresh_cookie(self, client):
        await _register(client, "logout@example.com")
        # Login sets the httponly refresh cookie
        login = await client.post(
            LOGIN_URL, json={"email": "logout@example.com", "password": "Password1!"}
        )
        assert login.status_code == 200

        logout = await client.post(
            LOGOUT_URL,
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert logout.status_code == 204

        # After logout, refresh should fail
        refresh = await client.post(REFRESH_URL)
        assert refresh.status_code == 401


class TestSoftDeleteAccount:
    async def test_delete_account_soft_deletes(self, client):
        tokens = await _register(client, "del@example.com")
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}

        r = await client.delete(ACCOUNT_URL, headers=auth)
        assert r.status_code == 204

        # Can no longer call /me (account_unavailable)
        me = await client.get(ME_URL, headers=auth)
        assert me.status_code == 401

    async def test_restore_account(self, client):
        tokens = await _register(client, "restore@example.com")
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}

        await client.delete(ACCOUNT_URL, headers=auth)
        r = await client.post("/v1/auth/account/restore", headers=auth)
        assert r.status_code == 204
