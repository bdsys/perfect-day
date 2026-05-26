"""Integration tests: trash (list soft-deleted) endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

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

        # Upgrade user to tier1 so they can hold 2 active diaries
        user_result = await db_session.execute(select(User).where(User.email == "tdl6@example.com"))
        user = user_result.scalar_one()
        user.subscription_tier = "tier1"
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
    # 2. Restoring a manual entry is blocked when at the manual cap
    # ------------------------------------------------------------------

    async def test_restore_manual_entry_blocked_at_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_login(client, "trtl-me1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)

        # Create 5 manual entries (free tier manual cap = 5)
        entries = []
        for i in range(5):
            e = await _create_entry(client, auth, diary["id"])
            entries.append(e)

        # Delete entry[0] — now 4 active
        await _delete_entry(client, auth, entries[0]["id"])

        # Create a 6th entry — now back at 5 active (cap)
        await _create_entry(client, auth, diary["id"])

        # Restoring entry[0] should now be blocked
        r = await client.post(f"/v1/entries/{entries[0]['id']}/restore", headers=auth)
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "tier_limit"
        assert detail["details"]["source"] == "manual"
        assert detail["details"]["limit"] == 5
        assert detail["details"]["current"] == 5

    # ------------------------------------------------------------------
    # 3. Restoring an auto entry is blocked when at the auto cap
    # ------------------------------------------------------------------

    async def test_restore_auto_entry_blocked_at_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_login(client, "trtl-ae1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        diary_id = uuid.UUID(diary["id"])

        # Insert 3 auto entries directly via DB (free tier auto cap = 3)
        auto_entries = []
        for i in range(3):
            entry = Entry(
                diary_id=diary_id,
                entry_date=date(2025, 6, i + 1),
                created_by="auto",
                status="draft",
            )
            db_session.add(entry)
            auto_entries.append(entry)
        await db_session.commit()
        # Refresh to get IDs
        for e in auto_entries:
            await db_session.refresh(e)

        # Delete auto_entries[0] via HTTP — now 2 active auto
        r = await client.delete(f"/v1/entries/{auto_entries[0].id}", headers=auth)
        assert r.status_code == 204

        # Insert one more auto entry via DB — back to 3 active auto (at cap)
        extra = Entry(
            diary_id=diary_id,
            entry_date=date(2025, 7, 1),
            created_by="auto",
            status="draft",
        )
        db_session.add(extra)
        await db_session.commit()

        # Restoring auto_entries[0] should be blocked
        r = await client.post(f"/v1/entries/{auto_entries[0].id}/restore", headers=auth)
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "tier_limit"
        assert detail["details"]["source"] == "auto"
        assert detail["details"]["limit"] == 3

    # ------------------------------------------------------------------
    # 4. Restoring a manual entry succeeds even when auto quota is full
    # ------------------------------------------------------------------

    async def test_restore_manual_succeeds_when_only_auto_full(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_login(client, "trtl-ms1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = await _create_diary(client, auth)
        diary_id = uuid.UUID(diary["id"])

        # Fill auto quota (3 auto entries)
        for i in range(3):
            db_session.add(
                Entry(
                    diary_id=diary_id,
                    entry_date=date(2025, 8, i + 1),
                    created_by="auto",
                    status="draft",
                )
            )
        await db_session.commit()

        # Create 1 manual entry via HTTP, then delete it
        manual_entry = await _create_entry(client, auth, diary["id"])
        await _delete_entry(client, auth, manual_entry["id"])

        # Restore the deleted manual entry — should succeed (auto cap full, but manual cap not)
        r = await client.post(f"/v1/entries/{manual_entry['id']}/restore", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is None

    # ------------------------------------------------------------------
    # 5. Tier limit check uses the diary owner's quota, not the caller's
    # ------------------------------------------------------------------

    async def test_restore_uses_diary_owner_quota_not_caller(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_email = "trtl-owner1@example.com"
        editor_email = "trtl-editor1@example.com"

        owner_token = await _register_and_login(client, owner_email)
        editor_token = await _register_and_login(client, editor_email)
        owner_auth = {"Authorization": f"Bearer {owner_token}"}
        editor_auth = {"Authorization": f"Bearer {editor_token}"}

        # Upgrade editor to tier1 (unlimited) — owner stays free (manual cap = 5)
        editor_user_result = await db_session.execute(
            select(User).where(User.email == editor_email)
        )
        editor_user = editor_user_result.scalar_one()
        editor_user.subscription_tier = "tier1"
        await db_session.commit()

        # Owner creates a diary and 5 manual entries (at cap)
        diary = await _create_diary(client, owner_auth)
        entries = []
        for i in range(5):
            e = await _create_entry(client, owner_auth, diary["id"])
            entries.append(e)

        # Delete entry[0] — 4 active
        await _delete_entry(client, owner_auth, entries[0]["id"])

        # Create a 6th entry to fill the slot back to 5 active
        await _create_entry(client, owner_auth, diary["id"])

        # Grant editor access to the diary
        perm = DiaryPermission(
            diary_id=uuid.UUID(diary["id"]),
            user_id=editor_user.id,
            role="editor",
        )
        db_session.add(perm)
        await db_session.commit()

        # Editor calls restore — should be 403 because the owner is at cap
        r = await client.post(f"/v1/entries/{entries[0]['id']}/restore", headers=editor_auth)
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "tier_limit"
        assert detail["details"]["source"] == "manual"
        assert detail["details"]["limit"] == 5

    # ------------------------------------------------------------------
    # 6. Restoring a diary succeeds when the user has headroom on tier1
    # ------------------------------------------------------------------

    async def test_restore_succeeds_for_unlimited_tier(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await _register_and_login(client, "trtl-t1a@example.com")
        auth = {"Authorization": f"Bearer {token}"}

        # Upgrade user to tier1 (allows 2 active diaries)
        user_result = await db_session.execute(
            select(User).where(User.email == "trtl-t1a@example.com")
        )
        user = user_result.scalar_one()
        user.subscription_tier = "tier1"
        await db_session.commit()

        # Create and delete diary A
        diary_a = await _create_diary(client, auth, "Diary A1")
        await _delete_diary(client, auth, diary_a["id"])

        # Create diary B — now 1 active diary (tier1 limit = 2, still has headroom)
        await _create_diary(client, auth, "Diary B1")

        # Restore diary A — should succeed (1 active < 2 limit)
        r = await client.post(f"/v1/diaries/{diary_a['id']}/restore", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is None
