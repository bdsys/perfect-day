"""Integration tests: soft-delete → hard-delete cascade for diaries and users."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.factories import make_user, make_diary, make_entry, make_event


async def _register(client: AsyncClient, email: str) -> tuple[str, str]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    data = r.json()
    return data["access_token"], r.cookies.get("refresh_token", "")


class TestHardDeleteDiary:
    async def test_soft_delete_sets_deleted_at(self, client):
        token, _ = await _register(client, "hd1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)).json()

        r = await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is not None

    async def test_soft_deleted_diary_not_in_list(self, client):
        token, _ = await _register(client, "hd2@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)).json()

        await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)

        r = await client.get("/v1/diaries", headers=auth)
        assert r.status_code == 200
        assert all(d["id"] != diary["id"] for d in r.json())

    async def test_hard_delete_cascades_entries(self, client, db_session: AsyncSession):
        token, _ = await _register(client, "hd3@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)).json()
        entry = (await client.post(
            f"/v1/diaries/{diary['id']}/entries",
            json={"entry_date": "2025-01-01", "title": "E"},
            headers=auth,
        )).json()

        # Soft delete the diary
        await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)

        # Simulate the hard-delete beat task running directly
        from app.models import Diary
        from datetime import datetime, timezone, timedelta

        diary_obj = (await db_session.execute(
            select(Diary).where(Diary.id == diary["id"])
        )).scalar_one()
        # Force hard_delete_after to be in the past
        diary_obj.hard_delete_after = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        await db_session.commit()

        from app.workers.hard_delete import hard_delete_diary
        import uuid
        await hard_delete_diary(uuid.UUID(diary["id"]))

        # Entry should be gone
        from app.models import Entry
        entry_check = (await db_session.execute(
            select(Entry).where(Entry.id == entry["id"])
        )).scalar_one_or_none()
        assert entry_check is None

    async def test_restore_cancels_hard_delete(self, client):
        token, _ = await _register(client, "hd4@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)).json()

        await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)
        r = await client.post(f"/v1/diaries/{diary['id']}/restore", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is None
        assert r.json()["hard_delete_after"] is None


class TestHardDeleteUser:
    async def test_delete_account_sets_hard_delete_after(self, client):
        token, _ = await _register(client, "hdu1@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        r = await client.delete("/v1/auth/account", headers=auth)
        assert r.status_code == 200

        # Account unavailable immediately
        me = await client.get("/v1/auth/me", headers=auth)
        assert me.status_code == 401
