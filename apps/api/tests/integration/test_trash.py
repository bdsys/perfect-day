"""Integration tests: trash (list soft-deleted) endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diary, DiaryPermission, Entry, User


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


    async def test_restore_gets_new_slug_when_original_taken(
        self, client, db_session: AsyncSession
    ):
        token = await _register_and_login(client, "tdl6@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Create and delete the original diary
        original = await _create_diary(client, auth, "My Diary")
        await _delete_diary(client, auth, original["id"])

        # Upgrade user to plus so they can hold 2 active diaries
        user_result = await db_session.execute(select(User).where(User.email == "tdl6@example.com"))
        user = user_result.scalar_one()
        user.subscription_tier = "plus"
        await db_session.commit()

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


class TestRestoreTierLimits:
    # ------------------------------------------------------------------
    # 1. Restoring a diary is blocked when the user is at the diary cap
    # ------------------------------------------------------------------

    async def test_restore_diary_blocked_at_free_tier_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_login(client, "trtl-d1@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Create and delete diary A
        diary_a = await _create_diary(client, auth, "Diary A")
        await _delete_diary(client, auth, diary_a["id"])

        # Create diary B — now 1 active diary, free tier cap is 1
        await _create_diary(client, auth, "Diary B")

        # Attempt to restore diary A — should be blocked (at cap)
        r = await client.post(f"/v1/diaries/{diary_a['id']}/restore", headers=auth)
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "tier_limit"
        assert detail["details"]["limit"] == 1
        assert detail["details"]["current"] == 1
        assert detail["details"]["source"] == "diary"

    # ------------------------------------------------------------------
    # 2. Restoring a diary succeeds when the user has headroom on plus
    # ------------------------------------------------------------------

    async def test_restore_succeeds_for_unlimited_tier(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_login(client, "trtl-t1a@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Upgrade user to plus (allows 2 active diaries)
        user_result = await db_session.execute(
            select(User).where(User.email == "trtl-t1a@example.com")
        )
        user = user_result.scalar_one()
        user.subscription_tier = "plus"
        await db_session.commit()

        # Create and delete diary A
        diary_a = await _create_diary(client, auth, "Diary A1")
        await _delete_diary(client, auth, diary_a["id"])

        # Create diary B — now 1 active diary (plus limit = 2, still has headroom)
        await _create_diary(client, auth, "Diary B1")

        # Restore diary A — should succeed (1 active < 2 limit)
        r = await client.post(f"/v1/diaries/{diary_a['id']}/restore", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is None


class TestRestoreEntryPermissions:
    """C-21: bringing an entry back from the trash is an editor's action."""

    async def _deleted_entry_shared_as(
        self, client: AsyncClient, db_session: AsyncSession, role: str, prefix: str
    ) -> tuple[dict, dict]:
        owner_token = await _register_and_login(client, f"{prefix}-owner@example.com")
        owner_auth = {"Authorization": f"Bearer {owner_token}"}
        other_email = f"{prefix}-{role}@example.com"
        other_auth = {"Authorization": f"Bearer {await _register_and_login(client, other_email)}"}
        diary = await _create_diary(client, owner_auth)
        entry = await _create_entry(client, owner_auth, diary["id"])
        await _delete_entry(client, owner_auth, entry["id"])
        result = await db_session.execute(select(User).where(User.email == other_email))
        other = result.scalar_one()
        db_session.add(
            DiaryPermission(diary_id=uuid.UUID(diary["id"]), user_id=other.id, role=role)
        )
        await db_session.commit()
        return entry, other_auth

    async def test_viewer_cannot_restore_a_deleted_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        entry, viewer_auth = await self._deleted_entry_shared_as(
            client, db_session, "viewer", "rep-v"
        )

        r = await client.post(f"/v1/entries/{entry['id']}/restore", headers=viewer_auth)

        assert r.status_code == 403
        db_session.expire_all()
        stored = (
            await db_session.execute(select(Entry).where(Entry.id == uuid.UUID(entry["id"])))
        ).scalar_one()
        assert stored.deleted_at is not None

    async def test_editor_can_restore_a_deleted_entry(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        entry, editor_auth = await self._deleted_entry_shared_as(
            client, db_session, "editor", "rep-e"
        )

        r = await client.post(f"/v1/entries/{entry['id']}/restore", headers=editor_auth)

        assert r.status_code == 200
        assert r.json()["deleted_at"] is None
