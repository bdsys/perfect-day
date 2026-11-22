"""Free allows 1 photo per entry; Plus and Family are unlimited. The plan is the diary owner's."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DiaryPermission, EntryPhoto, Photo, User
from app.services.photo_limits import ensure_photo_room
from tests.integration.photo_helpers import jpeg_with_exif, register_and_login, upload_photo
from tests.integration.test_photo_imports import (
    FakeGoogle,
    _connect_photos,
    _entry,
    _entry_photos,
    _google,
    _import_row,
    _photo,
    _run_worker,
    _start,
    _user,
)


async def _set_tier(db_session, email: str, tier: str) -> None:
    await db_session.execute(update(User).where(User.email == email).values(subscription_tier=tier))
    await db_session.commit()


async def _setup(client, prefix: str, tier: str | None = None, db_session=None):
    email = f"{prefix}@example.com"
    auth = await register_and_login(client, email)
    if tier is not None:
        await _set_tier(db_session, email, tier)
    _, entry = await _entry(client, auth)
    return auth, entry


async def _new_photo(client, auth) -> str:
    return await upload_photo(
        client,
        auth,
        jpeg_with_exif(taken=f"2025:08:14 13:{uuid.uuid4().int % 60:02d}:00") + uuid.uuid4().bytes,
    )


async def _attach(client, auth, entry_id: str, photo_id: str):
    return await client.post(
        f"/v1/entries/{entry_id}/photos", json={"photo_id": photo_id}, headers=auth
    )


async def _count(db_session, entry_id: str) -> int:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(func.count())
            .select_from(EntryPhoto)
            .where(EntryPhoto.entry_id == uuid.UUID(entry_id))
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_free_second_photo_blocked(client):
    auth, entry = await _setup(client, "pl-free")
    p1, p2 = await _new_photo(client, auth), await _new_photo(client, auth)
    assert (await _attach(client, auth, entry["id"], p1)).status_code == 201
    r = await _attach(client, auth, entry["id"], p2)
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "tier_limit"
    assert detail["details"]["source"] == "photo"
    assert detail["details"]["required_tier"] == "plus"
    assert detail["details"]["limit"] == 1
    assert detail["details"]["current"] == 1


@pytest.mark.asyncio
async def test_reattach_same_photo_is_not_counted(client):
    auth, entry = await _setup(client, "pl-reattach")
    p1 = await _new_photo(client, auth)
    assert (await _attach(client, auth, entry["id"], p1)).status_code == 201
    assert (await _attach(client, auth, entry["id"], p1)).status_code == 201


@pytest.mark.asyncio
async def test_plus_attaches_many(client, db_session):
    auth, entry = await _setup(client, "pl-plus", "plus", db_session)
    for _ in range(3):
        p = await _new_photo(client, auth)
        assert (await _attach(client, auth, entry["id"], p)).status_code == 201


@pytest.mark.asyncio
async def test_concurrent_attach_keeps_one_photo(client, db_engine, db_session):
    auth, entry = await _setup(client, "pl-race")
    p1, p2 = await _new_photo(client, auth), await _new_photo(client, auth)
    owner = await _user(db_session, "pl-race@example.com")
    entry_id = uuid.UUID(entry["id"])
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def attach(photo_id: str) -> int:
        # What the attach route does, in its own session: check for room, then add.
        async with factory() as s:
            try:
                await ensure_photo_room(s, entry_id, owner)
            except HTTPException as e:
                await s.rollback()
                return e.status_code
            s.add(EntryPhoto(entry_id=entry_id, photo_id=uuid.UUID(photo_id), position=0))
            # Hold the transaction open a moment so the racers overlap.
            await asyncio.sleep(0.05)
            await s.commit()
            return 201

    results = await asyncio.gather(attach(p1), attach(p2))
    assert sorted(results) == [201, 403]
    assert await _count(db_session, entry["id"]) == 1


@pytest.mark.asyncio
async def test_legacy_entry_keeps_its_photos(client, db_session):
    auth, entry = await _setup(client, "pl-legacy")
    owner = await _user(db_session, "pl-legacy@example.com")
    for i in range(3):
        photo = Photo(
            user_id=owner.id,
            s3_key=f"legacy/{uuid.uuid4()}",
            mime_type="image/jpeg",
            bytes=1,
            source="upload",
            finalized_at=datetime.now(tz=UTC),
        )
        db_session.add(photo)
        await db_session.flush()
        db_session.add(EntryPhoto(entry_id=uuid.UUID(entry["id"]), photo_id=photo.id, position=i))
    await db_session.commit()
    assert await _count(db_session, entry["id"]) == 3
    r = await _attach(client, auth, entry["id"], await _new_photo(client, auth))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["current"] == 3
    assert await _count(db_session, entry["id"]) == 3


@pytest.mark.asyncio
async def test_import_full_entry_403(client, db_session):
    auth, entry = await _setup(client, "pl-imp-full")
    await _connect_photos(db_session, "pl-imp-full@example.com")
    assert (
        await _attach(client, auth, entry["id"], await _new_photo(client, auth))
    ).status_code == 201
    fake = FakeGoogle()
    with _google(fake), patch("app.workers.tasks.process_photo_import.apply_async") as queued:
        r = await client.post(f"/v1/entries/{entry['id']}/photo-imports", headers=auth)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["details"]["source"] == "photo"
    assert fake.sessions == []
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_import_caps_picker_to_room(client, db_session):
    auth, entry = await _setup(client, "pl-imp-cap")
    await _connect_photos(db_session, "pl-imp-cap@example.com")
    fake = FakeGoogle()
    await _start(client, auth, entry["id"], fake)
    assert fake.max_items == [1]


@pytest.mark.asyncio
async def test_plus_import_keeps_full_picker(client, db_session):
    auth, entry = await _setup(client, "pl-imp-plus", "plus", db_session)
    await _connect_photos(db_session, "pl-imp-plus@example.com")
    fake = FakeGoogle()
    await _start(client, auth, entry["id"], fake)
    assert fake.max_items == [50]


@pytest.mark.asyncio
async def test_import_worker_stops_at_room(client, db_session):
    from tests.integration.photo_helpers import read_fixture

    auth, entry = await _setup(client, "pl-imp-worker")
    await _connect_photos(db_session, "pl-imp-worker@example.com")
    fake = FakeGoogle(
        [_photo(1, read_fixture("sample.jpg")), _photo(2, jpeg_with_exif() + b"second")]
    )
    imp = await _start(client, auth, entry["id"], fake)
    await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert row.status == "done"
    assert row.added_count == 1
    assert fake.downloads == ["g-item-1"]
    assert fake.deleted == ["sess-1"]
    assert len(await _entry_photos(db_session, entry["id"])) == 1


@pytest.mark.asyncio
async def test_editor_in_family_owner_diary_unlimited(client, db_session):
    # Editors only exist on Family, where photos per entry are unlimited.
    owner, entry = await _setup(client, "pl-ed-owner", "family", db_session)
    editor = await register_and_login(client, "pl-editor@example.com")
    editor_id = (await _user(db_session, "pl-editor@example.com")).id
    db_session.add(
        DiaryPermission(diary_id=uuid.UUID(entry["diary_id"]), user_id=editor_id, role="editor")
    )
    await db_session.commit()
    for _ in range(3):
        p = await _new_photo(client, editor)
        assert (await _attach(client, editor, entry["id"], p)).status_code == 201


@pytest.mark.asyncio
async def test_editor_in_free_owner_diary_acts_as_viewer(client, db_session):
    owner, entry = await _setup(client, "pl-edf-owner")
    await _set_tier(db_session, "pl-edf-owner@example.com", "free")
    editor = await register_and_login(client, "pl-edf-editor@example.com")
    await _set_tier(
        db_session, "pl-edf-editor@example.com", "plus"
    )  # the editor's plan is irrelevant
    editor_id = (await _user(db_session, "pl-edf-editor@example.com")).id
    db_session.add(
        DiaryPermission(diary_id=uuid.UUID(entry["diary_id"]), user_id=editor_id, role="editor")
    )
    await db_session.commit()
    # Off Family the editor acts as a viewer, so cannot attach at all.
    r = await _attach(client, editor, entry["id"], await _new_photo(client, editor))
    assert r.status_code == 403, r.text
