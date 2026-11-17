"""Profile API: /me carries the unit and avatar; PATCH /auth/me saves the unit and icon."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.integration.photo_helpers import register_and_login

ME = "/v1/auth/me"


async def test_me_defaults(client: AsyncClient) -> None:
    headers = await register_and_login(client, "profile-defaults@example.com")
    body = (await client.get(ME, headers=headers)).json()
    assert body["temperature_unit"] == "F"
    assert body["avatar"] == {"kind": "initial"}
    assert body["deletion_scheduled_for"] is None


async def test_patch_unit(client: AsyncClient) -> None:
    headers = await register_and_login(client, "profile-unit@example.com")
    r = await client.patch(ME, json={"temperature_unit": "C"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["temperature_unit"] == "C"
    assert (await client.get(ME, headers=headers)).json()["temperature_unit"] == "C"


async def test_patch_icon_then_initial(client: AsyncClient) -> None:
    headers = await register_and_login(client, "profile-icon@example.com")
    r = await client.patch(ME, json={"avatar_icon": "panda"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["avatar"] == {"kind": "icon", "name": "panda"}
    assert (await client.get(ME, headers=headers)).json()["avatar"] == {
        "kind": "icon",
        "name": "panda",
    }
    r = await client.patch(ME, json={"avatar_icon": None}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["avatar"] == {"kind": "initial"}


async def test_patch_absent_key_changes_nothing(client: AsyncClient) -> None:
    headers = await register_and_login(client, "profile-absent@example.com")
    await client.patch(ME, json={"avatar_icon": "cat"}, headers=headers)
    r = await client.patch(ME, json={"temperature_unit": "C"}, headers=headers)
    assert r.json()["avatar"] == {"kind": "icon", "name": "cat"}


async def test_choosing_icon_clears_photo(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await register_and_login(client, "profile-photo@example.com")
    await db_session.execute(
        update(User)
        .where(User.email == "profile-photo@example.com")
        .values(avatar_s3_key="u/avatar/x.jpg", avatar_dek_ciphertext=b"dek")
    )
    await db_session.commit()
    assert (await client.get(ME, headers=headers)).json()["avatar"] == {"kind": "photo"}
    r = await client.patch(ME, json={"avatar_icon": "dog"}, headers=headers)
    assert r.json()["avatar"] == {"kind": "icon", "name": "dog"}
    await db_session.rollback()
    row = (
        await db_session.execute(
            User.__table__.select().where(User.email == "profile-photo@example.com")
        )
    ).one()
    assert row.avatar_s3_key is None
    assert row.avatar_dek_ciphertext is None


@pytest.mark.parametrize(
    "body",
    [
        {"temperature_unit": "K"},
        {"temperature_unit": None},
        {"avatar_icon": "unicorn"},
        {"email": "x@y.z"},
    ],
)
async def test_patch_rejects(client: AsyncClient, body: dict[str, object]) -> None:
    headers = await register_and_login(client, "profile-rejects@example.com")
    r = await client.patch(ME, json=body, headers=headers)
    assert r.status_code == 422, r.text


async def test_patch_needs_active_account(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await register_and_login(client, "profile-pending@example.com")
    now = datetime.now(tz=UTC)
    await db_session.execute(
        update(User)
        .where(User.email == "profile-pending@example.com")
        .values(deleted_at=now, hard_delete_after=now + timedelta(days=7))
    )
    await db_session.commit()
    r = await client.patch(ME, json={"temperature_unit": "C"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["message"] == "account_pending_deletion"
    me = await client.get(ME, headers=headers)
    assert me.json()["deletion_scheduled_for"] is not None
