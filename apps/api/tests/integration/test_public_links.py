"""Public links: hashed lookup, encrypted copy, one dead-link answer."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core.security import hash_token
from app.services.public_links import (
    LINK_INACTIVE,
    active_link,
    create_link,
    link_expired,
    link_token,
    resolve_link,
    revoke_links,
)


@pytest.mark.asyncio
async def test_create_link_stores_hash_and_ciphertext_only(
    db_session, make_user, make_diary_for_user
):
    user = await make_user()
    diary = await make_diary_for_user(user)
    row, raw = await create_link(
        db_session, diary_id=diary.id, kind="wish_list", target_id=uuid.uuid4()
    )
    await db_session.commit()
    assert raw not in row.token_hash
    assert raw.encode() not in row.token_encrypted
    assert row.token_hash == hash_token(raw)
    assert link_token(row) == raw


@pytest.mark.asyncio
async def test_create_link_revokes_previous(db_session, make_user, make_diary_for_user):
    user = await make_user()
    diary = await make_diary_for_user(user)
    target = uuid.uuid4()
    first, old = await create_link(
        db_session, diary_id=diary.id, kind="wish_list", target_id=target
    )
    await db_session.commit()
    second, new = await create_link(
        db_session, diary_id=diary.id, kind="wish_list", target_id=target
    )
    await db_session.commit()
    await db_session.refresh(first)
    assert first.revoked_at is not None
    assert (await active_link(db_session, kind="wish_list", target_id=target)).id == second.id
    with pytest.raises(HTTPException) as e:
        await resolve_link(db_session, old)
    assert e.value.status_code == 404
    assert e.value.detail == LINK_INACTIVE
    link, d = await resolve_link(db_session, new)
    assert link.id == second.id and d.id == diary.id
    await revoke_links(db_session, kind="wish_list", target_id=target)
    await db_session.commit()
    assert await active_link(db_session, kind="wish_list", target_id=target) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "revoked", "deleted_diary", "blocked_owner"])
async def test_resolve_unknown_revoked_deleted_diary_blocked_owner(
    db_session, make_user, make_diary_for_user, state
):
    user = await make_user()
    diary = await make_diary_for_user(user)
    target = uuid.uuid4()
    _, raw = await create_link(db_session, diary_id=diary.id, kind="wish_list", target_id=target)
    if state == "unknown":
        raw = "not-a-real-token"
    elif state == "revoked":
        await revoke_links(db_session, kind="wish_list", target_id=target)
    elif state == "deleted_diary":
        diary.deleted_at = datetime.now(tz=UTC)
    else:
        user.deleted_at = datetime.now(tz=UTC)  # no deadline: blocked
    await db_session.commit()
    with pytest.raises(HTTPException) as e:
        await resolve_link(db_session, raw)
    assert e.value.status_code == 404
    assert e.value.detail == "link_inactive"


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_link_expired_uses_diary_timezone():
    d = date(2026, 3, 14)
    assert not link_expired(d, "Pacific/Auckland", _utc("2026-03-21T10:00:00Z"))
    assert link_expired(d, "Pacific/Auckland", _utc("2026-03-21T11:30:00Z"))
    assert not link_expired(d, "America/Los_Angeles", _utc("2026-03-22T06:00:00Z"))
    assert isinstance(timedelta(days=7), timedelta)
    assert isinstance(link_expired(d, "UTC"), bool)


@pytest.mark.asyncio
async def test_public_headers_on_errors(client):
    r = await client.get("/v1/public/nope")
    assert r.status_code == 404
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    other = await client.get("/healthz")
    assert "x-robots-tag" not in other.headers
