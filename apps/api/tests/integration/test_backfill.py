"""Integration tests: Calendar backfill endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.models import BackfillRun, User

pytestmark = pytest.mark.asyncio


async def _set_tier(db_session, email: str, tier: str) -> None:
    await db_session.execute(update(User).where(User.email == email).values(subscription_tier=tier))
    await db_session.commit()


async def _setup(
    client: AsyncClient, db_session, tier: str = "plus", email: str = "backfill@example.com"
) -> tuple[str, dict]:
    r = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "Password1!"},
    )
    await _set_tier(db_session, email, tier)
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, diary


class TestBackfillEndpoint:
    async def test_backfill_accepts_from_to_dates(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
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

    async def test_backfill_rejects_from_after_to(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"from_date": "2026-05-15", "to_date": "2026-05-01"},
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_rejects_range_over_365_days(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/scan/backfill",
            json={"from_date": "2024-01-01", "to_date": "2025-12-31"},
            headers=auth,
        )
        assert r.status_code == 422

    async def test_backfill_rejects_unknown_source(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
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

    async def test_backfill_accepts_explicit_calendar_source(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
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

    async def test_backfill_returns_409_when_scan_lock_held(self, client: AsyncClient, db_session):
        from app.core.dependencies import get_redis
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}

        r_redis = get_redis()
        lock_key = f"scan_lock:{diary['id']}"
        await r_redis.set(lock_key, "1", ex=300)
        try:
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )
        finally:
            await r_redis.delete(lock_key)

        assert r.status_code == 409
        assert r.headers["Retry-After"] == "60"

    async def test_backfill_non_owner_rejected(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
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


class TestBackfillGet:
    async def test_get_returns_run(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )
        run_id = r.json()["id"]

        r2 = await client.get(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run_id}",
            headers=auth,
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["id"] == run_id
        assert body["sources"] == ["google_calendar"]
        assert body["status"] == "pending"

    async def test_get_404_when_missing(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await client.get(
            f"/v1/diaries/{diary['id']}/scan/backfill/{fake_id}",
            headers=auth,
        )
        assert r.status_code == 404

    async def test_get_404_when_run_belongs_to_other_diary(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        # Use a second user to avoid free-tier single-diary limit
        r_b = await client.post(
            "/v1/auth/register",
            json={"email": "backfill-diaryb@example.com", "password": "Password1!"},
        )
        await _set_tier(db_session, "backfill-diaryb@example.com", "plus")
        token_b = r_b.json()["access_token"]
        auth_b = {"Authorization": f"Bearer {token_b}"}
        diary_b = (await client.post(
            "/v1/diaries", json={"name": "B", "timezone": "UTC"}, headers=auth_b
        )).json()
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary_b['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth_b,
            )
        run_id = r.json()["id"]

        r2 = await client.get(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run_id}",
            headers=auth,
        )
        assert r2.status_code == 404


class TestBackfillDelete:
    async def test_delete_pending_returns_cancelled(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )
        run_id = r.json()["id"]

        r2 = await client.delete(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run_id}",
            headers=auth,
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "cancelled"

    async def test_delete_already_cancelled_is_idempotent(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )
        run_id = r.json()["id"]
        # First cancel
        await client.delete(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run_id}", headers=auth
        )
        # Second cancel — should still be 200
        r3 = await client.delete(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run_id}", headers=auth
        )
        assert r3.status_code == 200
        assert r3.json()["status"] == "cancelled"

    async def test_delete_completed_run_returns_409(self, client: AsyncClient, db_session):
        import uuid as _uuid
        from datetime import date as _d

        from app.models import BackfillRun as _BackfillRun
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        run = _BackfillRun(
            diary_id=_uuid.UUID(diary["id"]),
            from_date=_d(2026, 5, 1),
            to_date=_d(2026, 5, 15),
            sources=["google_calendar"],
            status="completed",
        )
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)

        r = await client.delete(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run.id}",
            headers=auth,
        )
        assert r.status_code == 409

    async def test_delete_404_when_missing(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await client.delete(
            f"/v1/diaries/{diary['id']}/scan/backfill/{fake_id}",
            headers=auth,
        )
        assert r.status_code == 404

    async def test_delete_403_when_not_owner(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session)
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )
        run_id = r.json()["id"]

        r2 = await client.post(
            "/v1/auth/register",
            json={"email": "stranger@example.com", "password": "Password1!"},
        )
        other_auth = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        r3 = await client.delete(
            f"/v1/diaries/{diary['id']}/scan/backfill/{run_id}",
            headers=other_auth,
        )
        assert r3.status_code in (403, 404)


RANGE = {"from_date": "2026-05-01", "to_date": "2026-05-15"}


class TestBackfillPlanLimit:
    async def test_free_backfill_refused(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session, tier="free", email="bf-free@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay") as delay:
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill", json=RANGE, headers=auth
            )
        assert r.status_code == 403, r.text
        details = r.json()["detail"]["details"]
        assert details["source"] == "backfill"
        assert details["required_tier"] == "plus"
        delay.assert_not_called()
        count = select(func.count()).select_from(BackfillRun)
        runs = (await db_session.execute(count)).scalar_one()
        assert runs == 0

    async def test_plus_backfill_accepted(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session, tier="plus", email="bf-plus@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        with patch("app.workers.tasks.backfill_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill", json=RANGE, headers=auth
            )
        assert r.status_code == 202


class TestScanPastDaysCap:
    async def _post(self, client, diary, token, body):
        with patch("app.workers.tasks.scan_diary.delay"):
            return await client.post(
                f"/v1/diaries/{diary['id']}/scan/run",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )

    async def test_free_scan_past_days_capped(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session, tier="free", email="cap-free@example.com")
        r = await self._post(client, diary, token, {"past_days": 365})
        assert r.status_code == 403, r.text
        details = r.json()["detail"]["details"]
        assert details["source"] == "backfill"
        assert details["current"] == 365
        assert (await self._post(client, diary, token, {"past_days": 90})).status_code == 202
        assert (await self._post(client, diary, token, {"future_days": 365})).status_code == 202
        with patch("app.workers.tasks.scan_diary.delay"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/run",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 202

    async def test_plus_scan_past_days_uncapped(self, client: AsyncClient, db_session):
        token, diary = await _setup(client, db_session, tier="plus", email="cap-plus@example.com")
        assert (await self._post(client, diary, token, {"past_days": 365})).status_code == 202
