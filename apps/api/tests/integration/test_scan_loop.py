"""Integration tests: scan trigger — lock contention 409, task queued."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from unittest.mock import patch


async def _setup(client: AsyncClient, email: str = "scan@example.com") -> tuple[str, dict]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)).json()
    return token, diary


class TestScanTrigger:
    async def test_trigger_returns_202_when_no_lock(self, client):
        token, diary = await _setup(client, "scan202@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Mock the celery task so it doesn't actually run
        with patch("app.workers.tasks.scan_diary.delay") as mock_delay:
            mock_delay.return_value = None
            r = await client.post(f"/v1/diaries/{diary['id']}/scan/run", headers=auth)

        assert r.status_code == 202

    async def test_trigger_returns_409_when_lock_held(self, client):
        token, diary = await _setup(client, "scan409@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Simulate a lock being held by adding to Redis
        from app.core.dependencies import get_redis
        r = get_redis()
        lock_key = f"scan_lock:{diary['id']}"
        r.set(lock_key, "1", ex=1800)

        try:
            result = await client.post(f"/v1/diaries/{diary['id']}/scan/run", headers=auth)
            assert result.status_code == 409
        finally:
            r.delete(lock_key)

    async def test_scan_runs_list(self, client):
        token, diary = await _setup(client, "scanruns@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.get(f"/v1/diaries/{diary['id']}/scan/runs", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_scan_config_get(self, client):
        token, diary = await _setup(client, "scancfg@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.get(f"/v1/diaries/{diary['id']}/scan", headers=auth)
        assert r.status_code == 200
