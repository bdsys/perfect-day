"""A diary's home coordinates go to the owner only (TR-20), like photo and entry
coordinates (S-12). Co-parents and viewers get the place name."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import DiaryPermission, User


@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_co_parents_never_get_home_coordinates(client, db_session, role):
    r = await client.post(
        "/v1/auth/register",
        json={"email": f"home-own-{role}@example.com", "password": "Password1!"},
    )
    owner_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    diary = (
        await client.post(
            "/v1/diaries", json={"name": "Home", "timezone": "UTC"}, headers=owner_auth
        )
    ).json()
    patched = await client.patch(
        f"/v1/diaries/{diary['id']}",
        json={"lat": 47.6, "lon": -122.3, "location_name": "Seattle"},
        headers=owner_auth,
    )
    assert patched.status_code == 200
    assert (patched.json()["lat"], patched.json()["lon"]) == (47.6, -122.3)

    r = await client.post(
        "/v1/auth/register", json={"email": f"home-{role}@example.com", "password": "Password1!"}
    )
    other_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    other = (
        await db_session.execute(select(User).where(User.email == f"home-{role}@example.com"))
    ).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary["id"]), user_id=other.id, role=role))
    await db_session.commit()

    one = (await client.get(f"/v1/diaries/{diary['id']}", headers=other_auth)).json()
    listed = next(
        d for d in (await client.get("/v1/diaries", headers=other_auth)).json()
        if d["id"] == diary["id"]
    )  # fmt: skip
    for body in (one, listed):
        assert body["lat"] is None and body["lon"] is None
        assert body["location_name"] == "Seattle"

    # PATCH is owner-only (require_owner=True), so an editor is refused and no
    # PATCH response can leak coordinates to a co-parent.
    refused = await client.patch(
        f"/v1/diaries/{diary['id']}", json={"name": "Mine now"}, headers=other_auth
    )
    assert refused.status_code == 403

    mine = (await client.get(f"/v1/diaries/{diary['id']}", headers=owner_auth)).json()
    assert (mine["lat"], mine["lon"]) == (47.6, -122.3)
