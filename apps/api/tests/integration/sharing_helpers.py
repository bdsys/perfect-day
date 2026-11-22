"""Shared helpers for the diary-sharing integration tests."""

from __future__ import annotations

import httpx
from sqlalchemy import update

PASSWORD = "Password1!"  # noqa: S105


async def register(client, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def set_tier(email: str, tier: str) -> None:
    """Set a user's plan on the row, through the app's own session factory."""
    from app.core.database import get_session_factory
    from app.models import User

    async with get_session_factory()() as session:
        await session.execute(
            update(User).where(User.email == email).values(subscription_tier=tier)
        )
        await session.commit()


async def make_shared_diary(client, owner_email: str, tier: str = "free") -> tuple[dict, str]:
    """Register an owner, set their tier on the user row, create a diary."""
    headers = await register(client, owner_email)
    await set_tier(owner_email, tier)
    r = await client.post(
        "/v1/diaries", json={"name": "Shared", "timezone": "UTC"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return headers, r.json()["id"]


async def invite(client, headers, diary_id: str, email: str, role: str = "viewer") -> dict:
    r = await client.post(
        f"/v1/diaries/{diary_id}/invitations", json={"email": email, "role": role}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def accept(client, headers, token: str) -> httpx.Response:
    return await client.post(f"/v1/invitations/{token}/accept", headers=headers)
