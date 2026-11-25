"""Integration tests: soft-delete → hard-delete cascade for diaries and users."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _register(client: AsyncClient, email: str) -> tuple[str, str]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    data = r.json()
    return data["access_token"], r.cookies.get("refresh_token", "")


class TestHardDeleteDiary:
    async def test_soft_delete_sets_deleted_at(self, client):
        token, _ = await _register(client, "hd1@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()

        r = await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)
        assert r.status_code == 200
        assert r.json()["deleted_at"] is not None

    async def test_soft_deleted_diary_not_in_list(self, client):
        token, _ = await _register(client, "hd2@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()

        await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)

        r = await client.get("/v1/diaries", headers=auth)
        assert r.status_code == 200
        assert all(d["id"] != diary["id"] for d in r.json())

    async def test_hard_delete_cascades_entries(self, client, db_session: AsyncSession):
        token, _ = await _register(client, "hd3@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-01-01", "title": "E"},
                headers=auth,
            )
        ).json()

        # Soft delete the diary
        await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)

        # Simulate the hard-delete beat task running directly
        from app.models import Diary

        diary_obj = (
            await db_session.execute(select(Diary).where(Diary.id == diary["id"]))
        ).scalar_one()
        # Force hard_delete_after to be in the past
        diary_obj.hard_delete_after = datetime.now(tz=UTC) - timedelta(seconds=1)
        await db_session.commit()

        import uuid

        from app.workers.hard_delete import hard_delete_diary

        await hard_delete_diary(uuid.UUID(diary["id"]))

        # Entry should be gone
        from app.models import Entry

        entry_check = (
            await db_session.execute(select(Entry).where(Entry.id == entry["id"]))
        ).scalar_one_or_none()
        assert entry_check is None

    async def test_restore_cancels_hard_delete(self, client):
        token, _ = await _register(client, "hd4@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()

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

        # Pending deletion immediately: other endpoints refuse with 403, /me shows the deadline
        diaries = await client.get("/v1/diaries", headers=auth)
        assert diaries.status_code == 403
        me = await client.get("/v1/auth/me", headers=auth)
        assert me.status_code == 200
        assert me.json()["deletion_scheduled_for"] == r.json()["deletion_scheduled_for"]


class TestHardDeleteWishLists:
    async def test_hard_delete_removes_wish_lists_and_links(self, client, db_session):
        import uuid

        from sqlalchemy import func

        from app.models import (
            Diary,
            PublicLink,
            User,
            WishList,
            WishListClaim,
            WishListItem,
        )
        from app.workers.hard_delete import hard_delete_diary

        token, _ = await _register(client, "hdwl@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
        ).json()
        did = uuid.UUID(diary["id"])
        await db_session.execute(
            User.__table__.update()
            .where(User.email == "hdwl@example.com")
            .values(display_name="Mum")
        )
        await db_session.commit()
        wl = (
            await client.post(
                f"/v1/diaries/{diary['id']}/wish-lists",
                json={"title": "T", "list_date": "2026-12-14"},
                headers=auth,
            )
        ).json()
        item = (
            await client.post(f"/v1/wish-lists/{wl['id']}/items", json={"name": "I"}, headers=auth)
        ).json()
        r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=auth)
        assert r.status_code == 200, r.text
        db_session.add(WishListClaim(item_id=uuid.UUID(item["id"]), name="Gran"))
        await db_session.commit()

        await client.delete(f"/v1/diaries/{diary['id']}", headers=auth)
        await hard_delete_diary(did)

        db_session.expire_all()
        for model, col in (
            (WishList, WishList.diary_id == did),
            (PublicLink, PublicLink.diary_id == did),
            (WishListItem, WishListItem.wish_list_id == uuid.UUID(wl["id"])),
            (WishListClaim, WishListClaim.item_id == uuid.UUID(item["id"])),
        ):
            n = (
                await db_session.execute(select(func.count()).select_from(model).where(col))
            ).scalar_one()
            assert n == 0, model.__name__
        assert (
            await db_session.execute(select(Diary).where(Diary.id == did))
        ).scalar_one_or_none() is None


class TestHardDeleteClaimer:
    async def test_deleting_a_claimer_removes_only_their_claim(self, client, db_session):
        import uuid

        from sqlalchemy import update

        from app.models import User, WishListClaim
        from app.workers.hard_delete import hard_delete_user
        from tests.fixtures.factories import mark_due_for_hard_delete
        from tests.integration.sharing_helpers import accept, invite

        otok, _ = await _register(client, "hdcl-o@example.com")
        owner = {"Authorization": f"Bearer {otok}"}
        vtok, _ = await _register(client, "hdcl-v@example.com")
        viewer = {"Authorization": f"Bearer {vtok}"}
        diary = (
            await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=owner)
        ).json()
        inv = await invite(client, owner, diary["id"], "hdcl-v@example.com")
        assert (await accept(client, viewer, inv["token"])).status_code == 200
        await db_session.execute(
            update(User).where(User.email == "hdcl-o@example.com").values(display_name="Mum")
        )
        await db_session.commit()
        wl = (
            await client.post(
                f"/v1/diaries/{diary['id']}/wish-lists",
                json={"title": "T", "list_date": "2026-12-14"},
                headers=owner,
            )
        ).json()
        a = (
            await client.post(f"/v1/wish-lists/{wl['id']}/items", json={"name": "A"}, headers=owner)
        ).json()
        b = (
            await client.post(f"/v1/wish-lists/{wl['id']}/items", json={"name": "B"}, headers=owner)
        ).json()
        await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
        v = (
            await db_session.execute(select(User).where(User.email == "hdcl-v@example.com"))
        ).scalar_one()
        db_session.add_all(
            [
                WishListClaim(item_id=uuid.UUID(a["id"]), user_id=v.id),
                WishListClaim(item_id=uuid.UUID(b["id"]), name="Gran"),
            ]
        )
        await db_session.commit()

        await client.delete("/v1/auth/account", headers=viewer)
        await mark_due_for_hard_delete(db_session, v)
        await hard_delete_user(v.id)

        db_session.expire_all()
        rows = (await db_session.execute(select(WishListClaim))).scalars().all()
        assert [(str(c.item_id), c.name) for c in rows] == [(b["id"], "Gran")]
        r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
        assert r.status_code == 200
        items = {i["name"]: i for i in r.json()["items"]}
        assert items["A"]["taken"] is False
        assert items["A"]["claim_count"] == 0
        assert items["B"]["claim_count"] == 1
