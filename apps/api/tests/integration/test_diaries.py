"""Integration tests: diary CRUD."""

from __future__ import annotations

from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, email: str = "user@example.com") -> str:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return r.json()["access_token"]


class TestCreateDiary:
    async def test_create_returns_diary(self, client):
        token = await _register_and_login(client)
        r = await client.post(
            "/v1/diaries",
            json={"name": "My Diary", "timezone": "America/New_York"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "My Diary"
        assert "slug" in data
        assert data["timezone"] == "America/New_York"

    async def test_auto_slug_generated(self, client):
        token = await _register_and_login(client, "slug@example.com")
        r = await client.post(
            "/v1/diaries",
            json={"name": "Diary With Spaces", "timezone": "UTC"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert "diary-with-spaces" in r.json()["slug"]

    async def test_free_tier_limited_to_one_diary(self, client):
        token = await _register_and_login(client, "tier@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        r1 = await client.post("/v1/diaries", json={"name": "D1", "timezone": "UTC"}, headers=auth)
        assert r1.status_code == 201

        r2 = await client.post("/v1/diaries", json={"name": "D2", "timezone": "UTC"}, headers=auth)
        assert r2.status_code == 403

    async def test_unauthenticated_returns_401(self, client):
        r = await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"})
        assert r.status_code == 401


class TestListAndGet:
    async def test_list_returns_own_diaries(self, client):
        token = await _register_and_login(client, "list@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        await client.post("/v1/diaries", json={"name": "Mine", "timezone": "UTC"}, headers=auth)
        r = await client.get("/v1/diaries", headers=auth)
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_get_diary_by_id(self, client):
        token = await _register_and_login(client, "get@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        created = (
            await client.post("/v1/diaries", json={"name": "G", "timezone": "UTC"}, headers=auth)
        ).json()
        r = await client.get(f"/v1/diaries/{created['id']}", headers=auth)
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    async def test_other_user_cannot_see_diary(self, client):
        t1 = await _register_and_login(client, "owner@example.com")
        t2 = await _register_and_login(client, "stranger@example.com")
        created = (
            await client.post(
                "/v1/diaries",
                json={"name": "Secret", "timezone": "UTC"},
                headers={"Authorization": f"Bearer {t1}"},
            )
        ).json()
        r = await client.get(
            f"/v1/diaries/{created['id']}", headers={"Authorization": f"Bearer {t2}"}
        )
        assert r.status_code == 404


class TestSoftDeleteDiary:
    async def test_delete_and_restore(self, client):
        token = await _register_and_login(client, "deldiarytest@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "Del", "timezone": "UTC"}, headers=auth)
        ).json()

        r = await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is not None

        r2 = await client.post(f"/v1/diaries/{diary['id']}/restore", headers=auth)
        assert r2.status_code == 200
        assert r2.json()["deleted_at"] is None
