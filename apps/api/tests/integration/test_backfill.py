"""Integration tests: Calendar backfill endpoint."""

from __future__ import annotations

from unittest.mock import patch

from httpx import AsyncClient
import pytest

pytestmark = pytest.mark.asyncio


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
    async def test_backfill_accepts_from_to_dates(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )
        assert r.status_code == 202
        body = r.json()
        assert body["from_date"] == "2026-05-01"
        assert body["to_date"] == "2026-05-15"
        assert body["sources"] == ["google_calendar"]
        assert body["status"] == "pending"

    async def test_backfill_rejects_from_after_to(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"from_date": "2026-05-15", "to_date": "2026-05-01"},
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_rejects_range_over_365_days(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"from_date": "2024-01-01", "to_date": "2025-12-31"},
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_rejects_unknown_source(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={
                "from_date": "2026-05-01",
                "to_date": "2026-05-15",
                "sources": ["photos"],
            },
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_accepts_explicit_calendar_source(self, client: AsyncClient):
        token, diary = await _setup(client)
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={
                    "from_date": "2026-05-01",
                    "to_date": "2026-05-15",
                    "sources": ["google_calendar"],
                },
                headers=auth,
            )
        assert r.status_code == 202

    async def test_backfill_non_owner_rejected(self, client: AsyncClient):
        token, diary = await _setup(client)
        r2 = await client.post(
            "/v1/auth/register",
            json={"email": "backfill-other@example.com", "password": "Password1!"},
        )
        other_auth = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
            headers=other_auth,
        )
        assert r.status_code in (403, 404)
