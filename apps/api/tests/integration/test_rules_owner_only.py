"""Auto-creation rules are the diary owner's alone (finding S-14).

Rules run over the owner's synced Google Calendar, and the preview searches
every synced event of the last 90 days by title, location or attendee email.
So only the owner can list, create, read, change, delete, preview or apply
rules; co-parents, editors included, get 403 from every rule endpoint.
Decision: Andrew, 2026-11-12 ("rules owner-only").
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import DiaryPermission, User

CONDITION = {"op": "AND", "children": [{"field": "title", "op": "contains", "value": "soccer"}]}


async def _register(client, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _diary_with_rule_shared(client, db_session, role: str, prefix: str):
    owner_auth = await _register(client, f"{prefix}-owner@example.com")
    diary = (
        await client.post(
            "/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=owner_auth
        )
    ).json()
    rule = (
        await client.post(
            f"/v1/diaries/{diary['id']}/rules",
            json={"name": "Soccer", "condition": CONDITION},
            headers=owner_auth,
        )
    ).json()
    other_auth = await _register(client, f"{prefix}-{role}@example.com")
    other = (
        await db_session.execute(select(User).where(User.email == f"{prefix}-{role}@example.com"))
    ).scalar_one()
    db_session.add(DiaryPermission(diary_id=uuid.UUID(diary["id"]), user_id=other.id, role=role))
    await db_session.commit()
    return owner_auth, other_auth, diary, rule


def _every_rule_call(diary_id: str, rule_id: str):
    return [
        ("GET", f"/v1/diaries/{diary_id}/rules", None),
        ("POST", f"/v1/diaries/{diary_id}/rules", {"name": "Mine", "condition": CONDITION}),
        ("POST", f"/v1/diaries/{diary_id}/rules/preview", {"condition": CONDITION}),
        ("GET", f"/v1/rules/{rule_id}", None),
        ("PATCH", f"/v1/rules/{rule_id}", {"name": "Renamed"}),
        ("POST", f"/v1/rules/{rule_id}/apply", {"days": 30}),
        ("DELETE", f"/v1/rules/{rule_id}", None),
    ]


class TestRulesAreOwnerOnly:
    @pytest.mark.parametrize("role", ["viewer", "editor"])
    async def test_co_parent_gets_403_from_every_rule_endpoint(self, client, db_session, role):
        _, other_auth, diary, rule = await _diary_with_rule_shared(
            client, db_session, role, f"s14-{role}"
        )

        with patch("app.workers.tasks.apply_rule_backfill.delay") as queued:
            answers = {
                (method, url): (
                    await client.request(method, url, json=body, headers=other_auth)
                ).status_code
                for method, url, body in _every_rule_call(diary["id"], rule["id"])
            }

        assert answers == {key: 403 for key in answers}
        queued.assert_not_called()

    async def test_co_parent_changes_nothing(self, client, db_session):
        owner_auth, editor_auth, diary, rule = await _diary_with_rule_shared(
            client, db_session, "editor", "s14-nochange"
        )

        with patch("app.workers.tasks.apply_rule_backfill.delay"):
            for method, url, body in _every_rule_call(diary["id"], rule["id"]):
                await client.request(method, url, json=body, headers=editor_auth)

        rules = (await client.get(f"/v1/diaries/{diary['id']}/rules", headers=owner_auth)).json()
        assert [(r["id"], r["name"]) for r in rules] == [(rule["id"], "Soccer")]

    async def test_owner_still_uses_every_rule_endpoint(self, client, db_session):
        owner_auth, _, diary, rule = await _diary_with_rule_shared(
            client, db_session, "editor", "s14-owner"
        )

        with patch("app.workers.tasks.apply_rule_backfill.delay"):
            answers = [
                (await client.request(method, url, json=body, headers=owner_auth)).status_code
                for method, url, body in _every_rule_call(diary["id"], rule["id"])
            ]

        assert answers == [200, 201, 200, 200, 200, 200, 204]
