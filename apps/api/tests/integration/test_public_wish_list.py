"""The public wish-list API: open, claim and undo with nothing but the link."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.models import Diary, User, WishList, WishListClaim
from tests.integration.wish_list_helpers import (
    _add_item,
    _member,
    _new_list,
    _owner,
    _set_display_name,
    _token_of,
)

HEADERS = {
    "x-robots-tag": "noindex, nofollow",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


def _err(r) -> str:
    assert r.json()["error"]["code"] == "http_error", r.text
    return r.json()["error"]["message"]


def _assert_headers(r) -> None:
    for k, v in HEADERS.items():
        assert r.headers[k] == v


async def _published(client, owner, did, *items, **extra):
    wl = await _new_list(client, owner, did, **extra)
    made = [await _add_item(client, owner, wl["id"], **i) for i in items]
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    assert r.status_code == 200, r.text
    return wl, made, await _token_of(client, owner, wl["id"])


async def _sql(stmt) -> None:
    from app.core.database import get_session_factory

    async with get_session_factory()() as s:
        await s.execute(stmt)
        await s.commit()


async def test_public_page_shows_only_the_list(client):
    owner, did = await _owner(client, "pw1-o@example.com", name="Mum")
    await _sql(update(Diary).where(Diary.id == uuid.UUID(did)).values(subject_name="Zorbaquin"))
    viewer = await _member(client, owner, did, "pw1-v@example.com")
    await _set_display_name("pw1-v@example.com", "Grandma Jo")
    wl, (lego, cards), token = await _published(
        client,
        owner,
        did,
        {"name": "Lego", "url": "https://example.com/lego", "note": "blue"},
        {"name": "Cards", "many": True},
        pasted_list_url="https://example.com/list",
    )
    assert (
        await client.post(f"/v1/wish-list-items/{lego['id']}/claim", headers=viewer)
    ).status_code == 200
    r = await client.post(
        f"/v1/public/{token}/items/{cards['id']}/claims", json={"name": "Sam's mum"}
    )
    assert r.status_code == 201, r.text

    # A bearer header changes nothing.
    plain = await client.get(f"/v1/public/{token}")
    authed = await client.get(f"/v1/public/{token}", headers=owner)
    assert plain.status_code == 200, plain.text
    assert authed.json() == plain.json()
    _assert_headers(plain)

    for text in (plain.text, authed.text):
        for secret in ("Grandma Jo", "Sam", "Shared", "Zorbaquin", "pw1-o@example.com", "pw1-v@"):
            assert secret not in text
    body = plain.json()
    assert body["kind"] == "wish_list"
    page = body["wish_list"]
    assert set(page) == {"title", "list_date", "pasted_list_url", "shared_by", "items"}
    assert page["shared_by"] == "Mum"
    assert page["pasted_list_url"] == "https://example.com/list"
    items = {i["name"]: i for i in page["items"]}
    assert set(items["Lego"]) == {"id", "name", "url", "note", "many", "taken", "claim_count"}
    assert items["Lego"]["taken"] is True and items["Lego"]["claim_count"] == 1
    assert items["Cards"]["taken"] is False and items["Cards"]["claim_count"] == 1


async def test_public_claim_and_undo_with_secret(client):
    owner, did = await _owner(client, "pw2-o@example.com")
    wl, (item,), token = await _published(client, owner, did, {"name": "Lego"})
    r = await client.post(f"/v1/public/{token}/items/{item['id']}/claims", json={"name": " Gran "})
    assert r.status_code == 201, r.text
    cid, secret = r.json()["claim_id"], r.json()["secret"]
    page = (await client.get(f"/v1/public/{token}")).json()["wish_list"]["items"][0]
    assert page["taken"] is True
    again = await client.post(
        f"/v1/public/{token}/items/{item['id']}/claims", json={"name": "Auntie"}
    )
    assert again.status_code == 409 and _err(again) == "item_taken"

    url = f"/v1/public/{token}/claims/{cid}"
    for headers in ({"X-Claim-Secret": "wrong"}, {}):
        bad = await client.delete(url, headers=headers)
        assert bad.status_code == 404 and _err(bad) == "claim_not_found"
        _assert_headers(bad)
    ok = await client.delete(url, headers={"X-Claim-Secret": secret})
    assert ok.status_code == 204
    _assert_headers(ok)
    page = (await client.get(f"/v1/public/{token}")).json()["wish_list"]["items"][0]
    assert page["taken"] is False and page["claim_count"] == 0
    # The owner saw the name while it was there; it is stored trimmed.
    assert (await client.delete(url, headers={"X-Claim-Secret": secret})).status_code == 404


async def test_public_claim_item_from_another_list_404(client):
    owner, did = await _owner(client, "pw3-o@example.com")
    _, (a,), token_a = await _published(client, owner, did, {"name": "A"})
    _, (b,), token_b = await _published(client, owner, did, {"name": "B"})
    r = await client.post(f"/v1/public/{token_a}/items/{b['id']}/claims", json={"name": "Gran"})
    assert r.status_code == 404 and _err(r) == "link_inactive"
    _assert_headers(r)
    # Undo through the wrong list's link is claim_not_found.
    ok = await client.post(f"/v1/public/{token_b}/items/{b['id']}/claims", json={"name": "Gran"})
    cid, secret = ok.json()["claim_id"], ok.json()["secret"]
    r = await client.delete(
        f"/v1/public/{token_a}/claims/{cid}", headers={"X-Claim-Secret": secret}
    )
    assert r.status_code == 404 and _err(r) == "claim_not_found"
    assert (await client.get(f"/v1/public/{token_b}")).json()["wish_list"]["items"][0]["taken"]


@pytest.mark.parametrize(
    "state", ["link_off", "unpublished", "expired", "diary_deleted", "owner_blocked"]
)
async def test_public_link_dead_states(client, state):
    email = f"pw4-{state}@example.com"
    owner, did = await _owner(client, email)
    wl, (item,), token = await _published(client, owner, did, {"name": "Lego"})
    r = await client.post(f"/v1/public/{token}/items/{item['id']}/claims", json={"name": "Gran"})
    cid, secret = r.json()["claim_id"], r.json()["secret"]
    if state == "link_off":
        assert (
            await client.post(f"/v1/wish-lists/{wl['id']}/link/off", headers=owner)
        ).status_code == 200
    elif state == "unpublished":
        assert (
            await client.post(f"/v1/wish-lists/{wl['id']}/unpublish", headers=owner)
        ).status_code == 200
        # Unpublishing revokes the link; also cover a still-live link on a draft.
        await _sql(
            update(WishList).where(WishList.id == uuid.UUID(wl["id"])).values(status="draft")
        )
    elif state == "expired":
        old = (datetime.now(tz=UTC) - timedelta(days=8)).date()
        await _sql(update(WishList).where(WishList.id == uuid.UUID(wl["id"])).values(list_date=old))
    elif state == "diary_deleted":
        await _sql(
            update(Diary).where(Diary.id == uuid.UUID(did)).values(deleted_at=datetime.now(tz=UTC))
        )
    else:
        await _sql(update(User).where(User.email == email).values(deleted_at=datetime.now(tz=UTC)))

    responses = [
        await client.get(f"/v1/public/{token}"),
        await client.post(f"/v1/public/{token}/items/{item['id']}/claims", json={"name": "X"}),
        await client.delete(f"/v1/public/{token}/claims/{cid}", headers={"X-Claim-Secret": secret}),
    ]
    for r in responses:
        assert r.status_code == 404, r.text
        assert r.json() == {"error": {"code": "http_error", "message": "link_inactive"}} or (
            _err(r) == "link_inactive"
        )
        _assert_headers(r)


async def test_public_read_only_diary_still_works(client):
    from tests.integration.sharing_helpers import set_tier

    owner, _ = await _owner(client, "pw5-o@example.com", tier="plus")
    r = await client.post("/v1/diaries", json={"name": "Second", "timezone": "UTC"}, headers=owner)
    assert r.status_code == 201, r.text
    second = r.json()["id"]
    wl, (item,), token = await _published(client, owner, second, {"name": "Lego"})
    await set_tier("pw5-o@example.com", "free")
    r = await client.post(f"/v1/wish-lists/{wl['id']}/items", json={"name": "z"}, headers=owner)
    assert r.status_code == 403  # read-only confirmed
    assert (await client.get(f"/v1/public/{token}")).status_code == 200
    r = await client.post(f"/v1/public/{token}/items/{item['id']}/claims", json={"name": "Gran"})
    assert r.status_code == 201


async def test_public_page_shared_by_never_email(client):
    owner, did = await _owner(client, "pw6-o@example.com", name="Mum")
    _, _, token = await _published(client, owner, did, {"name": "Lego"})
    assert (await client.get(f"/v1/public/{token}")).json()["wish_list"]["shared_by"] == "Mum"
    await _set_display_name("pw6-o@example.com", None)
    r = await client.get(f"/v1/public/{token}")
    assert r.json()["wish_list"]["shared_by"] is None
    assert "pw6-o@example.com" not in r.text
    await _set_display_name("pw6-o@example.com", "   ")
    assert (await client.get(f"/v1/public/{token}")).json()["wish_list"]["shared_by"] is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
async def test_public_claim_name_validation(client, name):
    owner, did = await _owner(client, f"pw7-{len(name)}@example.com")
    _, (item,), token = await _published(client, owner, did, {"name": "Lego"})
    r = await client.post(f"/v1/public/{token}/items/{item['id']}/claims", json={"name": name})
    assert r.status_code == 422, r.text
    _assert_headers(r)
    n = await client.get(f"/v1/public/{token}")
    assert n.json()["wish_list"]["items"][0]["claim_count"] == 0
    assert WishListClaim  # imported for clarity of the model under test


async def test_public_claim_huge_name_refused_by_the_model(client):
    """A body-size bound before the service's 1-60 rule: 10 000 characters never reach it."""
    owner, did = await _owner(client, "pw7-huge@example.com")
    _, (item,), token = await _published(client, owner, did, {"name": "Lego"})
    r = await client.post(
        f"/v1/public/{token}/items/{item['id']}/claims", json={"name": "x" * 10_000}
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    _assert_headers(r)
