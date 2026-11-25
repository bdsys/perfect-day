"""Shared setup for the wish-list integration tests."""

from __future__ import annotations

from sqlalchemy import update

from app.models import User
from tests.integration.sharing_helpers import accept, invite, make_shared_diary, register


async def _set_display_name(email: str, name: str | None) -> None:
    from app.core.database import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(update(User).where(User.email == email).values(display_name=name))
        await session.commit()


async def _member(client, owner, did, email, role="viewer") -> dict:
    created = await invite(client, owner, did, email, role)
    headers = await register(client, email)
    assert (await accept(client, headers, created["token"])).status_code == 200
    return headers


async def _owner(client, email, tier="free", name="Mum"):
    headers, did = await make_shared_diary(client, email, tier)
    await _set_display_name(email, name)
    return headers, did


async def _new_list(client, headers, did, title="Emma's 6th birthday", **extra) -> dict:
    r = await client.post(
        f"/v1/diaries/{did}/wish-lists",
        json={"title": title, "list_date": "2026-12-14", **extra},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _add_item(client, headers, wid, name="Lego", **extra) -> dict:
    r = await client.post(
        f"/v1/wish-lists/{wid}/items", json={"name": name, **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _token_of(client, headers, wid) -> str:
    r = await client.get(f"/v1/wish-lists/{wid}", headers=headers)
    assert r.json()["link"] is not None, r.text
    return r.json()["link"]["token"]
