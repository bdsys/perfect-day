"""Regenerate gate and the plan blocks in /me/plan, diary and entry responses."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import AiDraftSpend, DiaryPermission, User
from app.services.ai_drafts import usage_period
from tests.fixtures.factories import make_diary, make_entry, make_user

DELAY = "app.workers.tasks.generate_entry_draft.delay"


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _spend(db: AsyncSession, owner: User, n: int) -> None:
    period = await usage_period(db, owner.id, datetime.now(UTC))
    for _ in range(n):
        db.add(AiDraftSpend(user_id=owner.id, month=period.month, key=uuid.uuid4().hex))
    await db.commit()


async def _free_world(db: AsyncSession, spent: int):
    owner = await make_user(db, subscription_tier="free")
    diary = await make_diary(db, owner)
    entry = await make_entry(db, diary)
    await _spend(db, owner, spent)
    return owner, diary, entry


async def test_regenerate_blocked_at_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    owner, _, entry = await _free_world(db_session, 5)
    resets_on = (await usage_period(db_session, owner.id)).resets_on
    with patch(DELAY, new=MagicMock()) as delay:
        r = await client.post(f"/v1/entries/{entry.id}/regenerate", headers=_auth(owner))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "tier_limit"
    assert r.json()["detail"]["details"] == {
        "limit": 5,
        "current": 5,
        "source": "ai_draft",
        "required_tier": "plus",
        "resets_on": resets_on.isoformat(),
    }
    delay.assert_not_called()


async def test_regenerate_allowed_below_limit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner, _, entry = await _free_world(db_session, 4)
    with patch(DELAY, new=MagicMock()) as delay:
        r = await client.post(f"/v1/entries/{entry.id}/regenerate", headers=_auth(owner))
    assert r.status_code == 200
    delay.assert_called_once()


async def test_editor_on_free_owner_diary_cannot_regenerate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner, diary, entry = await _free_world(db_session, 5)
    editor = await make_user(db_session, subscription_tier="family")
    db_session.add(DiaryPermission(diary_id=diary.id, user_id=editor.id, role="editor"))
    await db_session.commit()
    with patch(DELAY, new=MagicMock()) as delay:
        r = await client.post(f"/v1/entries/{entry.id}/regenerate", headers=_auth(editor))
    # Off Family the editor acts as a viewer, so the request is refused before any quota check.
    assert r.status_code == 403
    delay.assert_not_called()


async def test_me_plan_free(client: AsyncClient, db_session: AsyncSession) -> None:
    owner, diary, _ = await _free_world(db_session, 2)
    r = await client.get("/v1/auth/me/plan", headers=_auth(owner))
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "free"
    assert body["plan_name"] == "Free"
    assert body["diaries_limit"] == 1
    assert body["diaries_used"] == 1
    assert body["ai_drafts_limit"] == 5
    assert body["ai_drafts_used"] == 2
    assert body["ai_drafts_left"] == 3
    assert body["photos_per_entry"] == 1
    assert body["read_only_diary_ids"] == []
    assert date.fromisoformat(body["resets_on"]).day == 1


async def test_my_plan_reports_people_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    owner = await make_user(db_session, subscription_tier="free")
    r = await client.get("/v1/auth/me/plan", headers=_auth(owner))
    assert r.status_code == 200
    body = r.json()
    assert body["people_per_diary"] == 3
    assert body["editors"] is False


async def test_me_plan_plus(client: AsyncClient, db_session: AsyncSession) -> None:
    owner = await make_user(db_session, subscription_tier="plus")
    r = await client.get("/v1/auth/me/plan", headers=_auth(owner))
    body = r.json()
    assert body["tier"] == "plus"
    assert body["ai_drafts_limit"] is None
    assert body["ai_drafts_left"] is None
    assert body["photos_per_entry"] is None


async def test_diary_and_entry_detail_carry_owner_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner, diary, entry = await _free_world(db_session, 2)
    second = await make_diary(db_session, owner, name="Second Diary")  # over the Free limit
    editor = await make_user(db_session, subscription_tier="family")
    for d in (diary, second):
        db_session.add(DiaryPermission(diary_id=d.id, user_id=editor.id, role="editor"))
    await db_session.commit()
    second_entry = await make_entry(db_session, second)

    # An editor of a Free owner acts as a viewer, who gets no plan numbers.
    assert (await client.get(f"/v1/entries/{entry.id}", headers=_auth(editor))).json().get(
        "plan"
    ) is None

    d = (await client.get(f"/v1/diaries/{diary.id}", headers=_auth(owner))).json()
    assert d["plan"]["tier"] == "free"
    assert d["plan"]["ai_drafts_left"] == 3
    assert d["plan"]["read_only"] is False
    e = (await client.get(f"/v1/entries/{entry.id}", headers=_auth(owner))).json()
    assert e["plan"]["tier"] == "free"
    assert e["plan"]["ai_drafts_left"] == 3
    assert e["plan"]["read_only"] is False

    d2 = (await client.get(f"/v1/diaries/{second.id}", headers=_auth(owner))).json()
    e2 = (await client.get(f"/v1/entries/{second_entry.id}", headers=_auth(owner))).json()
    assert d2["plan"]["read_only"] is True
    assert e2["plan"]["read_only"] is True


async def test_plan_only_on_detail_gets(client: AsyncClient, db_session: AsyncSession) -> None:
    owner, _, _ = await _free_world(db_session, 0)
    rows = (await client.get("/v1/diaries", headers=_auth(owner))).json()
    assert all(row.get("plan") is None for row in rows)


async def test_viewer_never_sees_owner_plan(client: AsyncClient, db_session: AsyncSession) -> None:
    owner, diary, entry = await _free_world(db_session, 2)
    entry.status = "published"  # viewers only see published entries
    viewer = await make_user(db_session, subscription_tier="family")
    db_session.add(DiaryPermission(diary_id=diary.id, user_id=viewer.id, role="viewer"))
    await db_session.commit()

    d = await client.get(f"/v1/diaries/{diary.id}", headers=_auth(viewer))
    assert d.status_code == 200, d.text
    assert d.json()["plan"] is None
    e = await client.get(f"/v1/entries/{entry.id}", headers=_auth(viewer))
    assert e.status_code == 200, e.text
    assert e.json()["plan"] is None


async def test_owner_plan_backfill_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    free = await make_user(db_session, subscription_tier="free")
    free_diary = await make_diary(db_session, free)
    plus = await make_user(db_session, subscription_tier="plus")
    plus_diary = await make_diary(db_session, plus)

    d = (await client.get(f"/v1/diaries/{free_diary.id}", headers=_auth(free))).json()
    assert d["plan"]["backfill"] is False
    assert d["plan"]["scan_past_days_max"] == 90
    d = (await client.get(f"/v1/diaries/{plus_diary.id}", headers=_auth(plus))).json()
    assert d["plan"]["backfill"] is True
    assert d["plan"]["scan_past_days_max"] is None

    me = (await client.get("/v1/auth/me/plan", headers=_auth(free))).json()
    assert me["backfill"] is False
    assert me["scan_past_days_max"] == 90
    me = (await client.get("/v1/auth/me/plan", headers=_auth(plus))).json()
    assert me["backfill"] is True
    assert me["scan_past_days_max"] is None
