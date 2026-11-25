"""Wish lists: the owner's and editors' endpoints, and what viewers see."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.models import User, WishListClaim
from app.services.public_links import resolve_link
from tests.integration.sharing_helpers import set_tier
from tests.integration.wish_list_helpers import (
    _add_item,
    _member,
    _new_list,
    _owner,
    _set_display_name,
    _token_of,
)


async def _dead(db_session, token) -> bool:
    db_session.expire_all()  # the test session may hold the link from an earlier look
    try:
        await resolve_link(db_session, token)
    except HTTPException as e:
        return e.status_code == 404 and e.detail == "link_inactive"
    return False


async def test_owner_creates_lists_and_items_viewer_sees_published_only(client):
    owner, did = await _owner(client, "wl1-o@example.com")
    viewer = await _member(client, owner, did, "wl1-v@example.com")
    draft = await _new_list(client, owner, did, "Draft list")
    pub = await _new_list(client, owner, did, "Published list")
    item = await _add_item(client, owner, pub["id"], "Lego", url="https://shop.example/lego")
    assert item["taken"] is False
    assert item["claim_count"] == 0
    assert item["claims"] == []
    r = await client.post(f"/v1/wish-lists/{pub['id']}/publish", headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"
    assert r.json()["link"]["path"] == f"/p/{r.json()['link']['token']}"

    r = await client.get(f"/v1/diaries/{did}/wish-lists", headers=owner)
    assert {w["id"] for w in r.json()} == {draft["id"], pub["id"]}
    r = await client.get(f"/v1/diaries/{did}/wish-lists", headers=viewer)
    assert [w["id"] for w in r.json()] == [pub["id"]]

    assert (await client.get(f"/v1/wish-lists/{draft['id']}", headers=viewer)).status_code == 404
    r = await client.get(f"/v1/wish-lists/{pub['id']}", headers=viewer)
    assert r.status_code == 200
    assert r.json()["items"][0]["name"] == "Lego"
    assert r.json()["items"][0]["claims"] is None


async def test_viewer_cannot_create_edit_publish(client):
    owner, did = await _owner(client, "wl2-o@example.com")
    viewer = await _member(client, owner, did, "wl2-v@example.com")
    wl = await _new_list(client, owner, did)
    item = await _add_item(client, owner, wl["id"])
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    wid = wl["id"]
    calls = [
        ("POST", f"/v1/diaries/{did}/wish-lists", {"title": "x", "list_date": "2026-12-14"}),
        ("PATCH", f"/v1/wish-lists/{wid}", {"title": "y"}),
        ("DELETE", f"/v1/wish-lists/{wid}", None),
        ("POST", f"/v1/wish-lists/{wid}/items", {"name": "z"}),
        ("PATCH", f"/v1/wish-list-items/{item['id']}", {"name": "z"}),
        ("DELETE", f"/v1/wish-list-items/{item['id']}", None),
        ("POST", f"/v1/wish-lists/{wid}/publish", None),
        ("POST", f"/v1/wish-lists/{wid}/unpublish", None),
        ("POST", f"/v1/wish-lists/{wid}/link/off", None),
        ("POST", f"/v1/wish-lists/{wid}/link/new", None),
    ]
    for method, url, body in calls:
        r = await client.request(method, url, json=body, headers=viewer)
        assert r.status_code in (403, 404), (method, url, r.status_code)
    # Nothing changed.
    r = await client.get(f"/v1/wish-lists/{wid}", headers=owner)
    assert r.json()["title"] == wl["title"]
    assert r.json()["status"] == "published"
    assert len(r.json()["items"]) == 1


async def test_item_cap_100(client):
    owner, did = await _owner(client, "wl3-o@example.com")
    wl = await _new_list(client, owner, did)
    from app.core.database import get_session_factory
    from app.models import WishListItem

    async with get_session_factory()() as session:
        session.add_all(
            WishListItem(wish_list_id=uuid.UUID(wl["id"]), position=i, name=f"i{i}")
            for i in range(100)
        )
        await session.commit()
    r = await client.post(f"/v1/wish-lists/{wl['id']}/items", json={"name": "101"}, headers=owner)
    assert r.status_code == 409
    assert r.json() == {"error": {"code": "http_error", "message": "wish_list_full"}}


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "//shop.example"])
async def test_bad_urls_refused(client, bad):
    owner, did = await _owner(client, "wl4-o@example.com")
    wl = await _new_list(client, owner, did)
    r = await client.post(
        f"/v1/wish-lists/{wl['id']}/items", json={"name": "x", "url": bad}, headers=owner
    )
    assert r.status_code == 422
    r = await client.post(
        f"/v1/diaries/{did}/wish-lists",
        json={"title": "t", "list_date": "2026-12-14", "pasted_list_url": bad},
        headers=owner,
    )
    assert r.status_code == 422


async def test_url_is_trimmed_and_scheme_lowercased(client):
    owner, did = await _owner(client, "wl4b-o@example.com")
    wl = await _new_list(client, owner, did, pasted_list_url="  HTTPS://Shop.example/x ")
    assert wl["pasted_list_url"] == "https://Shop.example/x"
    item = await _add_item(client, owner, wl["id"], url="  HTTPS://Shop.example/x ")
    assert item["url"] == "https://Shop.example/x"
    r = await client.patch(
        f"/v1/wish-list-items/{item['id']}", json={"url": None, "name": "Renamed"}, headers=owner
    )
    assert r.json()["url"] is None
    assert r.json()["name"] == "Renamed"


async def test_publish_needs_owner_display_name(client):
    owner, did = await _owner(client, "wl5-o@example.com", tier="family", name="   ")
    editor = await _member(client, owner, did, "wl5-e@example.com", "editor")
    wl = await _new_list(client, owner, did)

    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    assert r.status_code == 409
    assert r.json() == {
        "detail": {"code": "display_name_required", "details": {"caller_is_owner": True}}
    }
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=editor)
    assert r.status_code == 409
    assert r.json()["detail"]["details"] == {"caller_is_owner": False}
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
    assert r.json()["status"] == "draft"

    await _set_display_name("wl5-o@example.com", "Mum")
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=editor)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"


async def test_publish_gives_link_new_link_kills_old(client, db_session):
    owner, did = await _owner(client, "wl6-o@example.com")
    wl = await _new_list(client, owner, did)
    r = await client.post(f"/v1/wish-lists/{wl['id']}/link/new", headers=owner)
    assert r.status_code == 409
    assert r.json()["error"]["message"] == "wish_list_not_published"

    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    old = r.json()["link"]["token"]
    _, diary = await resolve_link(db_session, old)
    assert str(diary.id) == did

    r = await client.post(f"/v1/wish-lists/{wl['id']}/link/new", headers=owner)
    assert r.status_code == 200, r.text
    new = r.json()["link"]["token"]
    assert new != old
    assert await _dead(db_session, old)
    await resolve_link(db_session, new)
    assert await _token_of(client, owner, wl["id"]) == new


async def test_unpublish_then_publish_gives_fresh_token(client, db_session):
    owner, did = await _owner(client, "wl7-o@example.com")
    wl = await _new_list(client, owner, did)
    first = (await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)).json()["link"][
        "token"
    ]
    r = await client.post(f"/v1/wish-lists/{wl['id']}/unpublish", headers=owner)
    assert r.status_code == 200
    assert r.json()["status"] == "draft"
    assert r.json()["link"] is None
    assert await _dead(db_session, first)
    second = (await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)).json()[
        "link"
    ]["token"]
    assert second != first
    assert await _dead(db_session, first)


async def test_link_off_keeps_list_published(client, db_session):
    owner, did = await _owner(client, "wl7b-o@example.com")
    wl = await _new_list(client, owner, did)
    tok = (await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)).json()["link"][
        "token"
    ]
    r = await client.post(f"/v1/wish-lists/{wl['id']}/link/off", headers=owner)
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    assert r.json()["link"] is None
    assert await _dead(db_session, tok)


async def test_read_only_diary_blocks_edits_allows_unpublish_and_link_off(client):
    owner, did = await _owner(client, "wl8-o@example.com", tier="plus")
    r = await client.post("/v1/diaries", json={"name": "Second", "timezone": "UTC"}, headers=owner)
    assert r.status_code == 201, r.text
    second = r.json()["id"]
    wl = await _new_list(client, owner, second)
    item = await _add_item(client, owner, wl["id"])
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    await set_tier("wl8-o@example.com", "free")  # the second diary is now read-only

    blocked = [
        ("POST", f"/v1/diaries/{second}/wish-lists", {"title": "t", "list_date": "2026-12-14"}),
        ("PATCH", f"/v1/wish-lists/{wl['id']}", {"title": "new"}),
        ("POST", f"/v1/wish-lists/{wl['id']}/items", {"name": "z"}),
        ("PATCH", f"/v1/wish-list-items/{item['id']}", {"name": "z"}),
        ("DELETE", f"/v1/wish-list-items/{item['id']}", None),
        ("POST", f"/v1/wish-lists/{wl['id']}/publish", None),
        ("POST", f"/v1/wish-lists/{wl['id']}/link/new", None),
    ]
    for method, url, body in blocked:
        r = await client.request(method, url, json=body, headers=owner)
        assert r.status_code == 403, (method, url, r.status_code, r.text)
        assert r.json()["detail"]["code"] == "tier_limit"

    assert (await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)).status_code == 200
    r = await client.post(f"/v1/wish-lists/{wl['id']}/link/off", headers=owner)
    assert r.status_code == 200
    r = await client.post(f"/v1/wish-lists/{wl['id']}/unpublish", headers=owner)
    assert r.status_code == 200
    r = await client.delete(f"/v1/wish-lists/{wl['id']}", headers=owner)
    assert r.status_code == 204


async def test_link_not_returned_to_viewers(client):
    owner, did = await _owner(client, "wl9-o@example.com")
    viewer = await _member(client, owner, did, "wl9-v@example.com")
    wl = await _new_list(client, owner, did)
    await _add_item(client, owner, wl["id"])
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=viewer)
    assert r.json()["link"] is None
    assert "token" not in r.text


async def test_claims_serialized_by_role(client, db_session):
    owner, did = await _owner(client, "wl10-o@example.com")
    viewer = await _member(client, owner, did, "wl10-v@example.com")
    wl = await _new_list(client, owner, did)
    one = await _add_item(client, owner, wl["id"], "One")
    many = await _add_item(client, owner, wl["id"], "Many", many=True)
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    from sqlalchemy import select

    v = (
        await db_session.execute(select(User).where(User.email == "wl10-v@example.com"))
    ).scalar_one()
    db_session.add_all(
        [
            WishListClaim(item_id=uuid.UUID(one["id"]), user_id=v.id),
            WishListClaim(item_id=uuid.UUID(many["id"]), name="Gran"),
        ]
    )
    await db_session.commit()

    items = {
        i["name"]: i
        for i in (await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)).json()["items"]
    }
    assert items["One"]["taken"] is True
    assert items["One"]["mine"] is False
    assert items["One"]["claims"][0]["signed_in"] is True
    assert items["One"]["claims"][0]["name"] == "wl10-v@example.com"
    assert items["Many"]["taken"] is False
    assert items["Many"]["claim_count"] == 1
    assert items["Many"]["claims"][0] == {
        "id": items["Many"]["claims"][0]["id"],
        "name": "Gran",
        "signed_in": False,
        "created_at": items["Many"]["claims"][0]["created_at"],
    }
    vitems = {
        i["name"]: i
        for i in (await client.get(f"/v1/wish-lists/{wl['id']}", headers=viewer)).json()["items"]
    }
    assert vitems["One"]["mine"] is True
    assert vitems["One"]["taken"] is True
    assert vitems["One"]["claims"] is None
    assert vitems["Many"]["mine"] is False


async def test_delete_list_removes_link_rows(client, db_session):
    from sqlalchemy import func, select

    from app.models import PublicLink

    owner, did = await _owner(client, "wl11-o@example.com")
    wl = await _new_list(client, owner, did)
    await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    await client.post(f"/v1/wish-lists/{wl['id']}/link/new", headers=owner)
    assert (await client.delete(f"/v1/wish-lists/{wl['id']}", headers=owner)).status_code == 204
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(PublicLink)
            .where(PublicLink.target_id == uuid.UUID(wl["id"]))
        )
    ).scalar_one()
    assert n == 0
    assert (await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)).status_code == 404


async def test_patch_list_and_items_positions(client):
    owner, did = await _owner(client, "wl12-o@example.com")
    wl = await _new_list(client, owner, did)
    a = await _add_item(client, owner, wl["id"], "A")
    b = await _add_item(client, owner, wl["id"], "B")
    r = await client.patch(
        f"/v1/wish-lists/{wl['id']}",
        json={"title": "New", "list_date": "2026-12-20"},
        headers=owner,
    )
    assert r.status_code == 200
    assert r.json()["title"] == "New"
    assert r.json()["list_date"] == "2026-12-20"
    assert [i["name"] for i in r.json()["items"]] == ["A", "B"]
    await client.delete(f"/v1/wish-list-items/{a['id']}", headers=owner)
    r = await client.get(f"/v1/wish-lists/{wl['id']}", headers=owner)
    assert [i["id"] for i in r.json()["items"]] == [b["id"]]


async def test_publish_twice_keeps_the_same_link(client, db_session):
    owner, did = await _owner(client, "wl13-o@example.com")
    wl = await _new_list(client, owner, did)
    first = (await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)).json()
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    assert r.status_code == 200
    assert r.json()["link"]["token"] == first["link"]["token"]
    db_session.expire_all()
    await resolve_link(db_session, first["link"]["token"])
    # A link turned off stays off on re-publish; link/new is what makes a new one.
    await client.post(f"/v1/wish-lists/{wl['id']}/link/off", headers=owner)
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    assert r.status_code == 200
    assert r.json()["link"] is None
    # No display-name check on a no-op re-publish.
    await _set_display_name("wl13-o@example.com", None)
    assert (
        await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    ).status_code == 200


async def test_patch_item_with_signed_in_claim(client, db_session):
    from sqlalchemy import select

    owner, did = await _owner(client, "wl14-o@example.com")
    await _member(client, owner, did, "wl14-v@example.com")
    wl = await _new_list(client, owner, did)
    item = await _add_item(client, owner, wl["id"], "One")
    v = (
        await db_session.execute(select(User).where(User.email == "wl14-v@example.com"))
    ).scalar_one()
    db_session.add(WishListClaim(item_id=uuid.UUID(item["id"]), user_id=v.id))
    await db_session.commit()
    r = await client.patch(
        f"/v1/wish-list-items/{item['id']}", json={"note": "blue"}, headers=owner
    )
    assert r.status_code == 200, r.text
    assert r.json()["claims"][0]["name"] == "wl14-v@example.com"
    assert r.json()["claims"][0]["signed_in"] is True
