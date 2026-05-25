"""Integration tests: trash (list soft-deleted) endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diary


async def _register_and_login(client: AsyncClient, email: str) -> str:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return r.json()["access_token"]


async def _create_diary(client: AsyncClient, auth: dict, name: str = "Test Diary") -> dict:
    r = await client.post("/v1/diaries", json={"name": name, "timezone": "UTC"}, headers=auth)
    assert r.status_code == 201
    return r.json()


async def _delete_diary(client: AsyncClient, auth: dict, diary_id: str) -> dict:
    r = await client.delete(f"/v1/diaries/{diary_id}", headers=auth)
    assert r.status_code == 200
    return r.json()


async def _create_entry(client: AsyncClient, auth: dict, diary_id: str) -> dict:
    r = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": "2024-01-15"},
        headers=auth,
    )
    assert r.status_code == 201
    return r.json()


async def _delete_entry(client: AsyncClient, auth: dict, entry_id: str) -> None:
    r = await client.delete(f"/v1/entries/{entry_id}", headers=auth)
    assert r.status_code == 204


class TestListDeletedDiaries:
    async def test_deleted_diary_appears_in_trash(self, client):
        token = await _register_and_login(client, "tdl1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        await _delete_diary(client, auth, diary["id"])

        r = await client.get("/v1/diaries/trash", headers=auth)
        assert r.status_code == 200
        assert any(d["id"] == diary["id"] for d in r.json())

    async def test_active_diary_not_in_trash(self, client):
        token = await _register_and_login(client, "tdl2@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)

        r = await client.get("/v1/diaries/trash", headers=auth)
        assert r.status_code == 200
        assert all(d["id"] != diary["id"] for d in r.json())

    async def test_other_user_cannot_see_deleted_diary(self, client):
        t1 = await _register_and_login(client, "tdl3a@example.com")
        t2 = await _register_and_login(client, "tdl3b@example.com")
        diary = await _create_diary(client, {"Authorization": f"Bearer {t1}"})
        await _delete_diary(client, {"Authorization": f"Bearer {t1}"}, diary["id"])

        r = await client.get("/v1/diaries/trash", headers={"Authorization": f"Bearer {t2}"})
        assert r.status_code == 200
        assert all(d["id"] != diary["id"] for d in r.json())

    async def test_unauthenticated_returns_401(self, client):
        r = await client.get("/v1/diaries/trash")
        assert r.status_code == 401

    async def test_response_includes_hard_delete_after(self, client):
        token = await _register_and_login(client, "tdl4@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        await _delete_diary(client, auth, diary["id"])

        r = await client.get("/v1/diaries/trash", headers=auth)
        assert r.status_code == 200
        item = next(d for d in r.json() if d["id"] == diary["id"])
        assert item["hard_delete_after"] is not None

    async def test_expired_grace_excluded(self, client, db_session: AsyncSession):
        token = await _register_and_login(client, "tdl5@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        await _delete_diary(client, auth, diary["id"])

        diary_obj = (
            await db_session.execute(select(Diary).where(Diary.id == uuid.UUID(diary["id"])))
        ).scalar_one()
        diary_obj.hard_delete_after = datetime.now(tz=UTC) - timedelta(days=1)
        await db_session.commit()

        r = await client.get("/v1/diaries/trash", headers=auth)
        assert r.status_code == 200
        assert all(d["id"] != diary["id"] for d in r.json())


    async def test_restore_gets_new_slug_when_original_taken(self, client):
        token = await _register_and_login(client, "tdl6@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Create and delete the original diary
        original = await _create_diary(client, auth, "My Diary")
        await _delete_diary(client, auth, original["id"])

        # Create a new diary with the same name — gets slug "my-diary" since deleted rows
        # are included in the slug uniqueness check, so it gets "my-diary-1"
        replacement = await _create_diary(client, auth, "My Diary")

        # Now restore the original — its slug "my-diary" is taken, so it should get a new one
        r = await client.post(f"/v1/diaries/{original['id']}/restore", headers=auth)
        assert r.status_code == 200
        restored = r.json()
        assert restored["deleted_at"] is None
        # Both diaries are active, slugs must differ
        assert restored["slug"] != replacement["slug"]


    async def test_deleted_entry_appears_in_trash(self, client):
        token = await _register_and_login(client, "tel1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])
        await _delete_entry(client, auth, entry["id"])

        r = await client.get(f"/v1/diaries/{diary['id']}/entries/trash", headers=auth)
        assert r.status_code == 200
        assert any(e["id"] == entry["id"] for e in r.json())

    async def test_active_entry_not_in_trash(self, client):
        token = await _register_and_login(client, "tel2@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])

        r = await client.get(f"/v1/diaries/{diary['id']}/entries/trash", headers=auth)
        assert r.status_code == 200
        assert all(e["id"] != entry["id"] for e in r.json())

    async def test_other_diary_not_accessible(self, client):
        token = await _register_and_login(client, "tel3@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        fake_id = str(uuid.uuid4())

        r = await client.get(f"/v1/diaries/{fake_id}/entries/trash", headers=auth)
        assert r.status_code == 404

    async def test_other_user_cannot_access_diary_trash(self, client):
        t1 = await _register_and_login(client, "tel4a@example.com")
        t2 = await _register_and_login(client, "tel4b@example.com")
        diary = await _create_diary(client, {"Authorization": f"Bearer {t1}"})

        r = await client.get(
            f"/v1/diaries/{diary['id']}/entries/trash",
            headers={"Authorization": f"Bearer {t2}"},
        )
        assert r.status_code == 404

    async def test_entries_trash_unauthenticated_returns_401(self, client):
        r = await client.get(f"/v1/diaries/{uuid.uuid4()}/entries/trash")
        assert r.status_code == 401

    async def test_deleted_entry_has_deleted_at(self, client):
        token = await _register_and_login(client, "tel5@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        entry = await _create_entry(client, auth, diary["id"])
        await _delete_entry(client, auth, entry["id"])

        r = await client.get(f"/v1/diaries/{diary['id']}/entries/trash", headers=auth)
        assert r.status_code == 200
        item = next(e for e in r.json() if e["id"] == entry["id"])
        assert item["deleted_at"] is not None

    async def test_entries_from_other_diary_do_not_appear(self, client):
        token = await _register_and_login(client, "tel6@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary1 = await _create_diary(client, auth, "Diary X")
        entry = await _create_entry(client, auth, diary1["id"])
        await _delete_entry(client, auth, entry["id"])

        # Deleted entry from diary1 should not show under a random diary UUID
        r = await client.get(
            f"/v1/diaries/{uuid.uuid4()}/entries/trash", headers=auth
        )
        assert r.status_code == 404
