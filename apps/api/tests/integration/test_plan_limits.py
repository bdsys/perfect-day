"""Plan limits at the API: manual entries unlimited, diary caps per plan, rules unthrottled."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutoCreationRule, Entry, User
from app.workers.rules import evaluate_event_against_rules
from tests.fixtures.factories import make_event


async def _register(client: AsyncClient, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _set_tier(db: AsyncSession, email: str, tier: str) -> None:
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    user.subscription_tier = tier
    await db.commit()


async def _diary(client: AsyncClient, auth: dict, name: str):
    return await client.post("/v1/diaries", json={"name": name, "timezone": "UTC"}, headers=auth)


async def test_free_user_creates_sixth_manual_entry(client: AsyncClient) -> None:
    auth = await _register(client, "pl-free@example.com")
    diary = (await _diary(client, auth, "D")).json()
    for day in range(1, 7):
        r = await client.post(
            f"/v1/diaries/{diary['id']}/entries",
            json={"entry_date": f"2025-06-{day:02d}"},
            headers=auth,
        )
        assert r.status_code == 201


async def test_plus_second_diary_allowed_third_blocked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    auth = await _register(client, "pl-plus@example.com")
    await _set_tier(db_session, "pl-plus@example.com", "plus")
    assert (await _diary(client, auth, "A")).status_code == 201
    assert (await _diary(client, auth, "B")).status_code == 201
    r = await _diary(client, auth, "C")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "tier_limit"
    assert detail["details"] == {
        "source": "diary",
        "limit": 2,
        "current": 2,
        "required_tier": "family",
    }


async def test_family_fourth_diary_allowed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    auth = await _register(client, "pl-family@example.com")
    await _set_tier(db_session, "pl-family@example.com", "family")
    for name in "ABCD":
        assert (await _diary(client, auth, name)).status_code == 201


async def test_free_diary_limit_says_plus(client: AsyncClient) -> None:
    auth = await _register(client, "pl-free2@example.com")
    assert (await _diary(client, auth, "A")).status_code == 201
    r = await _diary(client, auth, "B")
    assert r.status_code == 403
    assert r.json()["detail"]["details"]["required_tier"] == "plus"


async def test_rule_creates_entry_past_old_auto_limit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    auth = await _register(client, "pl-rule@example.com")
    diary_id = uuid.UUID((await _diary(client, auth, "D")).json()["id"])
    for i in range(3):
        db_session.add(
            Entry(
                diary_id=diary_id,
                entry_date=datetime(2025, 5, i + 1).date(),
                created_by="auto",
                status="draft",
            )
        )
    db_session.add(
        AutoCreationRule(
            diary_id=diary_id,
            name="meetings",
            condition={
                "op": "AND",
                "children": [
                    {
                        "field": "title",
                        "op": "contains",
                        "value": "Meeting",
                        "case_sensitive": False,
                    }
                ],
            },
            options={"recurring": "per_instance", "multi_day": "per_day"},
            enabled=True,
        )
    )
    await db_session.commit()
    ev = await make_event(
        db_session,
        diary_id=diary_id,
        payload={
            "summary": "Meeting 4",
            "start": {"dateTime": "2026-06-04T10:00:00Z"},
            "end": {},
            "attendees": [],
            "description": "",
            "location": "",
            "status": "",
        },
        occurred_at=datetime(2026, 6, 4, 10, 0, tzinfo=UTC),
    )
    await db_session.commit()

    with patch("app.workers.tasks.generate_entry_draft") as mock_task:
        mock_task.delay = MagicMock()
        await evaluate_event_against_rules(str(ev.id), str(diary_id), db_session)

    rule_entries = (
        (
            await db_session.execute(
                select(Entry).where(Entry.diary_id == diary_id, Entry.creation_source == "rule")
            )
        )
        .scalars()
        .all()
    )
    assert len(rule_entries) == 1
    assert mock_task.delay.call_count == 1
