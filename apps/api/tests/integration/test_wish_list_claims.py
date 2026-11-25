"""Wish lists: claiming items, undoing and releasing claims, and "Coming up"."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models import Diary, User, WishListClaim
from app.services.wish_lists import MAX_CLAIMS_PER_ITEM, claim_item, coming_up, undo_claim
from tests.integration.sharing_helpers import set_tier
from tests.integration.wish_list_helpers import _add_item, _member, _new_list, _owner


async def _published(client, owner, did, *items, **extra) -> tuple[dict, list[dict]]:
    wl = await _new_list(client, owner, did, **extra)
    made = [await _add_item(client, owner, wl["id"], **i) for i in items]
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    assert r.status_code == 200, r.text
    return wl, made


def _err(r) -> str:
    assert r.json()["error"]["code"] == "http_error", r.text
    return r.json()["error"]["message"]


async def test_viewer_claims_single_item_others_see_taken_not_who(client):
    owner, did = await _owner(client, "wc1-o@example.com", name="Mum")
    v1 = await _member(client, owner, did, "wc1-v1@example.com")
    v2 = await _member(client, owner, did, "wc1-v2@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Lego"})

    r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=v1)
    assert r.status_code == 200, r.text
    assert r.json()["mine"] is True
    assert r.json()["taken"] is True
    assert r.json()["claims"] is None

    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=v2)
    it = r.json()["items"][0]
    assert (it["taken"], it["claims"], it["claim_count"], it["mine"]) == (True, None, 1, False)

    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
    claims = r.json()["items"][0]["claims"]
    assert len(claims) == 1 and claims[0]["signed_in"] is True and claims[0]["name"]

    # A second claimant on a single item is refused; the same user twice, too.
    r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=v2)
    assert r.status_code == 409 and _err(r) == "item_taken"


async def test_already_claimed_and_undo(client):
    owner, did = await _owner(client, "wc2-o@example.com")
    v = await _member(client, owner, did, "wc2-v@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Book", "many": True})
    url = f"/v1/wish-list-items/{item['id']}/claim"
    assert (await client.post(url, headers=v)).status_code == 200
    r = await client.post(url, headers=v)
    assert r.status_code == 409 and _err(r) == "already_claimed"
    r = await client.delete(url, headers=v)
    assert r.status_code == 200 and r.json()["mine"] is False and r.json()["claim_count"] == 0
    r = await client.delete(url, headers=v)
    assert r.status_code == 404 and _err(r) == "claim_not_found"


async def test_many_item_counts_never_taken(client):
    owner, did = await _owner(client, "wc3-o@example.com")
    v1 = await _member(client, owner, did, "wc3-v1@example.com")
    v2 = await _member(client, owner, did, "wc3-v2@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Cards", "many": True})
    for h in (v1, v2):
        r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=h)
        assert r.status_code == 200, r.text
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=v1)
    it = r.json()["items"][0]
    assert it["taken"] is False and it["claim_count"] == 2


async def test_manager_can_claim(client):
    owner, did = await _owner(client, "wc4-o@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Lego"})
    r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=owner)
    assert r.status_code == 200
    assert r.json()["claims"][0]["signed_in"] is True


async def test_claim_on_draft_404(client):
    owner, did = await _owner(client, "wc5-o@example.com")
    v = await _member(client, owner, did, "wc5-v@example.com")
    wl = await _new_list(client, owner, did)
    item = await _add_item(client, owner, wl["id"])
    for h in (v, owner):
        r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=h)
        assert r.status_code == 404, r.text
    # And a stranger gets 404 on a published one.
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    from tests.integration.sharing_helpers import register

    other = await register(client, "wc5-x@example.com")
    r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=other)
    assert r.status_code == 404


async def test_owner_releases_any_claim_viewer_cannot(client, db_session):
    owner, did = await _owner(client, "wc6-o@example.com")
    v1 = await _member(client, owner, did, "wc6-v1@example.com")
    v2 = await _member(client, owner, did, "wc6-v2@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Cards", "many": True})
    await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=v1)
    await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=v2)
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
    cid = r.json()["items"][0]["claims"][0]["id"]

    r = await client.delete(f"/v1/wish-list-claims/{cid}", headers=v2)
    assert r.status_code in (403, 404)
    r = await client.delete(f"/v1/wish-list-claims/{cid}", headers=owner)
    assert r.status_code == 204, r.text
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
    assert r.json()["items"][0]["claim_count"] == 1
    r = await client.delete(f"/v1/wish-list-claims/{cid}", headers=owner)
    assert r.status_code == 404 and _err(r) == "claim_not_found"
    r = await client.delete(f"/v1/wish-list-claims/{uuid.uuid4()}", headers=owner)
    assert r.status_code == 404


async def test_claims_cap_50(client, db_session):
    owner, did = await _owner(client, "wc7-o@example.com")
    v = await _member(client, owner, did, "wc7-v@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Cards", "many": True})
    iid = uuid.UUID(item["id"])
    for i in range(MAX_CLAIMS_PER_ITEM):
        db_session.add(WishListClaim(item_id=iid, name=f"Guest {i}"))
    await db_session.commit()
    r = await client.post(f"/v1/wish-list-items/{item['id']}/claim", headers=v)
    assert r.status_code == 409 and _err(r) == "item_claims_full"


async def test_claims_allowed_on_read_only_diary(client):
    owner, _ = await _owner(client, "wc8-o@example.com", tier="plus")
    r = await client.post("/v1/diaries", json={"name": "Second", "timezone": "UTC"}, headers=owner)
    assert r.status_code == 201, r.text
    second = r.json()["id"]
    v = await _member(client, owner, second, "wc8-v@example.com")
    wl, (item,) = await _published(client, owner, second, {"name": "Cards", "many": True})
    await set_tier("wc8-o@example.com", "free")  # the second diary is now read-only
    r = await client.post(f"/v1/wish-lists/{wl['id']}/items", json={"name": "z"}, headers=owner)
    assert r.status_code == 403  # confirms it is read-only
    url = f"/v1/wish-list-items/{item['id']}/claim"
    assert (await client.post(url, headers=v)).status_code == 200
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
    cid = r.json()["items"][0]["claims"][0]["id"]
    assert (await client.delete(url, headers=v)).status_code == 200
    assert (await client.post(url, headers=v)).status_code == 200
    assert (await client.post(url, headers=owner)).status_code == 200
    assert (await client.delete(f"/v1/wish-list-claims/{cid}", headers=owner)).status_code in (
        204,
        404,
    )


async def test_concurrent_claims_one_wins(client, db_engine):
    owner, did = await _owner(client, "wc9-o@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Lego"})
    iid = uuid.UUID(item["id"])
    async with AsyncSession(db_engine) as s1, AsyncSession(db_engine) as s2:
        # s1 claims and holds the item lock (uncommitted); s2 must wait, then see the claim.
        await claim_item(s1, iid, name="Gran")
        second = asyncio.create_task(claim_item(s2, iid, name="Auntie"))
        await asyncio.sleep(0.3)
        assert not second.done()  # blocked on s1's FOR UPDATE
        await s1.commit()
        with pytest.raises(HTTPException) as exc:
            await second
        assert exc.value.status_code == 409 and exc.value.detail == "item_taken"
        await s2.rollback()
    async with AsyncSession(db_engine) as s:
        n = (await s.execute(select(WishListClaim).where(WishListClaim.item_id == iid))).all()
    assert len(n) == 1


async def test_concurrent_claims_gather(client, db_engine):
    owner, did = await _owner(client, "wc10-o@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Lego"})
    iid = uuid.UUID(item["id"])

    async def one(name):
        async with AsyncSession(db_engine) as s:
            try:
                await claim_item(s, iid, name=name)
                await s.commit()
                return "ok"
            except HTTPException as e:
                await s.rollback()
                return e.detail

    results = await asyncio.gather(one("A"), one("B"))
    assert sorted(results) == ["item_taken", "ok"]


async def test_public_claim_and_undo_with_secret(client, db_engine):
    owner, did = await _owner(client, "wc11-o@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Cards", "many": True})
    iid = uuid.UUID(item["id"])
    async with AsyncSession(db_engine, expire_on_commit=False) as s:
        claim, secret = await claim_item(s, iid, name="  Gran  ")
        await s.commit()
        assert secret and len(secret) >= 32
        assert claim.name == "Gran" and claim.user_id is None
        assert claim.secret_hash == hash_token(secret) and claim.secret_hash != secret
        with pytest.raises(HTTPException) as e:
            await undo_claim(s, claim.id, secret="wrong")  # noqa: S106
        assert e.value.status_code == 404 and e.value.detail == "claim_not_found"
        with pytest.raises(HTTPException):
            await undo_claim(s, claim.id)  # neither user nor secret
        await undo_claim(s, claim.id, secret=secret)
        await s.commit()
        assert (await s.get(WishListClaim, claim.id)) is None


async def test_claim_item_input_rules(client, db_engine):
    owner, did = await _owner(client, "wc12-o@example.com")
    wl, (item,) = await _published(client, owner, did, {"name": "Cards", "many": True})
    iid = uuid.UUID(item["id"])
    async with AsyncSession(db_engine) as s:
        for bad in ("", "   ", "x" * 61):
            with pytest.raises(HTTPException) as e:
                await claim_item(s, iid, name=bad)
            assert e.value.status_code == 422 and e.value.detail == "invalid_name"
        with pytest.raises(ValueError):
            await claim_item(s, iid)
        user = (await s.execute(select(User).limit(1))).scalar_one()
        with pytest.raises(ValueError):
            await claim_item(s, iid, user=user, name="Both")
        _, ok = await claim_item(s, iid, name="x" * 60)
        assert ok
        await s.rollback()


async def test_coming_up_window_and_timezone(client, db_engine, db_session):
    owner, did = await _owner(client, "wc13-o@example.com")
    await db_session.execute(
        text("UPDATE diaries SET timezone='Pacific/Auckland' WHERE id=:i"), {"i": did}
    )
    await db_session.commit()
    spec = [("1 Mar", "2026-03-01"), ("2 Mar", "2026-03-02"), ("13 Apr", "2026-04-13")]
    spec += [("14 Apr", "2026-04-14")]
    for title, d in reversed(spec):
        wl = await _new_list(client, owner, did, title, list_date=d)
        await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    await _new_list(client, owner, did, "Draft", list_date="2026-03-03")
    now = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)  # 2 Mar 09:00 in Auckland
    async with AsyncSession(db_engine) as s:
        diary = await s.get(Diary, uuid.UUID(did))
        got = await coming_up(s, diary, now)
    assert [w.title for w in got] == ["2 Mar", "13 Apr"]


async def test_coming_up_endpoint_members_only(client):
    owner, did = await _owner(client, "wc14-o@example.com")
    v = await _member(client, owner, did, "wc14-v@example.com")
    today = datetime.now(tz=UTC).date().isoformat()
    wl = await _new_list(client, owner, did, "Soon", list_date="2999-01-01")
    far = await _new_list(client, owner, did, "Today-ish", list_date=today)
    await client.post(f"/v1/wish-lists/{far['id']}/publish", headers=owner)
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    for h in (owner, v):
        r = await client.get(f"/v1/diaries/{did}/coming-up", headers=h)
        assert r.status_code == 200, r.text
        assert [w["title"] for w in r.json()["wish_lists"]] == ["Today-ish"]
        assert set(r.json()["wish_lists"][0]) >= {"id", "title", "list_date", "item_count"}
    from tests.integration.sharing_helpers import register

    other = await register(client, "wc14-x@example.com")
    assert (await client.get(f"/v1/diaries/{did}/coming-up", headers=other)).status_code == 404
