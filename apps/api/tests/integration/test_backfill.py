"""Integration tests: Calendar backfill endpoint."""

from __future__ import annotations

from unittest.mock import patch

from httpx import AsyncClient


async def _setup(client: AsyncClient) -> tuple[str, dict]:
    r = await client.post(
        "/v1/auth/register",
        json={"email": "backfill@example.com", "password": "Password1!"},
    )
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, diary


class TestBackfillEndpoint:
    async def test_backfill_returns_202_and_creates_run(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}

        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"days": 30},
                headers=auth,
            )

        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "pending"
        assert data["diary_id"] == diary["id"]
        assert "id" in data

    async def test_backfill_days_must_be_positive(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"days": 0},
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_days_max_365(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"days": 366},
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_non_owner_rejected(self, client: AsyncClient):
        token, diary = await _setup(client)

        # Create a second user
        r2 = await client.post(
            "/v1/auth/register",
            json={"email": "backfill-other@example.com", "password": "Password1!"},
        )
        other_auth = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"days": 30},
            headers=other_auth,
        )
        assert r.status_code in (403, 404)
