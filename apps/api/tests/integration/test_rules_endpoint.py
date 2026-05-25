"""Integration tests for auto-creation rules CRUD, preview, and apply endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.factories import make_event


async def _setup(client: AsyncClient, email: str):
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, auth, diary


_SIMPLE_CONDITION = {
    "op": "AND",
    "children": [
        {"field": "title", "op": "contains", "value": "soccer"}
    ],
}


class TestListRules:
    async def test_list_rules_empty(self, client: AsyncClient, db_session: AsyncSession):
        """Empty list for a fresh diary."""
        _, auth, diary = await _setup(client, "rules-list-empty@example.com")

        r = await client.get(f"/v1/diaries/{diary['id']}/rules", headers=auth)
        assert r.status_code == 200
        assert r.json() == []

    async def test_create_and_list_rule(self, client: AsyncClient, db_session: AsyncSession):
        """Create a rule, then list — it shows up."""
        _, auth, diary = await _setup(client, "rules-create-list@example.com")

        body = {"name": "Soccer rule", "condition": _SIMPLE_CONDITION}
        r = await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        assert r.status_code == 201
        created = r.json()
        assert created["name"] == "Soccer rule"
        assert created["enabled"] is True
        assert created["diary_id"] == diary["id"]

        r2 = await client.get(f"/v1/diaries/{diary['id']}/rules", headers=auth)
        assert r2.status_code == 200
        items = r2.json()
        assert len(items) == 1
        assert items[0]["id"] == created["id"]


class TestCreateRule:
    async def test_create_rule_validates_condition(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Empty leaf value must be rejected with 422."""
        _, auth, diary = await _setup(client, "rules-validate@example.com")

        bad_body = {
            "name": "bad rule",
            "condition": {
                "op": "AND",
                "children": [{"field": "title", "op": "contains", "value": ""}],
            },
        }
        r = await client.post(f"/v1/diaries/{diary['id']}/rules", json=bad_body, headers=auth)
        assert r.status_code == 422

    async def test_create_rule_unknown_field_422(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Unknown field in leaf should 422."""
        _, auth, diary = await _setup(client, "rules-bad-field@example.com")

        bad_body = {
            "name": "bad field",
            "condition": {"field": "nonexistent_field", "op": "contains", "value": "x"},
        }
        r = await client.post(f"/v1/diaries/{diary['id']}/rules", json=bad_body, headers=auth)
        assert r.status_code == 422


class TestGetRule:
    async def test_get_rule(self, client: AsyncClient, db_session: AsyncSession):
        """GET /v1/rules/{id} returns the correct rule."""
        _, auth, diary = await _setup(client, "rules-get@example.com")

        body = {"name": "Get rule", "condition": _SIMPLE_CONDITION}
        created = (
            await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        ).json()

        r = await client.get(f"/v1/rules/{created['id']}", headers=auth)
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]
        assert r.json()["name"] == "Get rule"

    async def test_get_rule_404(self, client: AsyncClient, db_session: AsyncSession):
        """Non-existent rule returns 404."""
        _, auth, _ = await _setup(client, "rules-get-404@example.com")

        r = await client.get(f"/v1/rules/{uuid.uuid4()}", headers=auth)
        assert r.status_code == 404


class TestPatchRule:
    async def test_patch_rule(self, client: AsyncClient, db_session: AsyncSession):
        """PATCH name field is updated; other fields unchanged."""
        _, auth, diary = await _setup(client, "rules-patch@example.com")

        body = {"name": "Original name", "condition": _SIMPLE_CONDITION}
        created = (
            await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        ).json()

        r = await client.patch(
            f"/v1/rules/{created['id']}", json={"name": "Updated name"}, headers=auth
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Updated name"
        # condition should be unchanged
        assert r.json()["condition"] == _SIMPLE_CONDITION

    async def test_patch_rule_condition_validates(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """PATCH with a bad condition returns 422."""
        _, auth, diary = await _setup(client, "rules-patch-validate@example.com")

        body = {"name": "Original", "condition": _SIMPLE_CONDITION}
        created = (
            await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        ).json()

        r = await client.patch(
            f"/v1/rules/{created['id']}",
            json={"condition": {"field": "title", "op": "contains", "value": ""}},
            headers=auth,
        )
        assert r.status_code == 422


class TestDeleteRule:
    async def test_delete_rule(self, client: AsyncClient, db_session: AsyncSession):
        """DELETE → 204, then GET → 404."""
        _, auth, diary = await _setup(client, "rules-delete@example.com")

        body = {"name": "Delete me", "condition": _SIMPLE_CONDITION}
        created = (
            await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        ).json()
        rule_id = created["id"]

        r = await client.delete(f"/v1/rules/{rule_id}", headers=auth)
        assert r.status_code == 204

        r2 = await client.get(f"/v1/rules/{rule_id}", headers=auth)
        assert r2.status_code == 404


class TestPreviewRule:
    async def test_preview_returns_matches(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Seed 3 events (2 matching), preview returns matched_count=2."""
        _, auth, diary = await _setup(client, "rules-preview-matches@example.com")
        diary_id = uuid.UUID(diary["id"])

        recent = datetime.now(tz=UTC) - timedelta(days=5)

        await make_event(
            db_session,
            diary_id=diary_id,
            payload={"summary": "soccer practice", "location": "", "description": "",
                     "start": {}, "end": {}, "status": "", "attendees": []},
            occurred_at=recent,
        )
        await make_event(
            db_session,
            diary_id=diary_id,
            payload={"summary": "Soccer tournament", "location": "Stadium",
                     "description": "", "start": {}, "end": {}, "status": "", "attendees": []},
            occurred_at=recent,
        )
        await make_event(
            db_session,
            diary_id=diary_id,
            payload={"summary": "Piano lesson", "location": "", "description": "",
                     "start": {}, "end": {}, "status": "", "attendees": []},
            occurred_at=recent,
        )

        r = await client.post(
            f"/v1/diaries/{diary['id']}/rules/preview",
            json={"condition": _SIMPLE_CONDITION},
            headers=auth,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["matched_count"] == 2
        assert data["total_evaluated"] == 3
        assert data["threshold_exceeded"] is False
        assert len(data["sample"]) == 2

    async def test_preview_threshold_exceeded(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Seed 31 matching events — threshold_exceeded should be True."""
        _, auth, diary = await _setup(client, "rules-preview-threshold@example.com")
        diary_id = uuid.UUID(diary["id"])

        recent = datetime.now(tz=UTC) - timedelta(days=5)
        for i in range(31):
            await make_event(
                db_session,
                diary_id=diary_id,
                payload={"summary": f"soccer game {i}", "location": "", "description": "",
                         "start": {}, "end": {}, "status": "", "attendees": []},
                occurred_at=recent,
            )

        r = await client.post(
            f"/v1/diaries/{diary['id']}/rules/preview",
            json={"condition": _SIMPLE_CONDITION},
            headers=auth,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["matched_count"] == 31
        assert data["threshold_exceeded"] is True
        # sample is capped at 10
        assert len(data["sample"]) == 10

    async def test_preview_validates_condition(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Bad condition returns 422."""
        _, auth, diary = await _setup(client, "rules-preview-validate@example.com")

        r = await client.post(
            f"/v1/diaries/{diary['id']}/rules/preview",
            json={"condition": {"field": "title", "op": "contains", "value": ""}},
            headers=auth,
        )
        assert r.status_code == 422


class TestApplyRule:
    async def test_apply_queues_backfill(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """POST /v1/rules/{id}/apply queues the backfill Celery task."""
        _, auth, diary = await _setup(client, "rules-apply@example.com")

        body = {"name": "Apply rule", "condition": _SIMPLE_CONDITION}
        created = (
            await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        ).json()

        with patch("app.workers.tasks.apply_rule_backfill") as mock_celery_task:
            mock_celery_task.delay = MagicMock()
            r = await client.post(
                f"/v1/rules/{created['id']}/apply",
                json={"days": 30},
                headers=auth,
            )

        assert r.status_code == 200
        assert r.json()["queued"] is True
        mock_celery_task.delay.assert_called_once()

    async def test_apply_broker_down_still_200(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """If the broker is down, apply returns 200 anyway (best-effort)."""
        _, auth, diary = await _setup(client, "rules-apply-broker-down@example.com")

        body = {"name": "Broker down rule", "condition": _SIMPLE_CONDITION}
        created = (
            await client.post(f"/v1/diaries/{diary['id']}/rules", json=body, headers=auth)
        ).json()

        with patch("app.workers.tasks.apply_rule_backfill") as mock_task:
            mock_task.delay.side_effect = Exception("broker unavailable")
            r = await client.post(
                f"/v1/rules/{created['id']}/apply",
                json={"days": 7},
                headers=auth,
            )

        assert r.status_code == 200
        assert r.json()["queued"] is True


class TestRuleIsolation:
    async def test_cannot_access_other_users_rule(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """User B cannot access a rule belonging to User A's diary."""
        # User A creates a rule
        _, auth_a, diary_a = await _setup(client, "rules-idor-user-a@example.com")
        rule = (
            await client.post(
                f"/v1/diaries/{diary_a['id']}/rules",
                json={
                    "name": "A rule",
                    "condition": {"op": "AND", "children": [
                        {
                            "field": "title",
                            "op": "contains",
                            "value": "test",
                            "case_sensitive": False,
                        }
                    ]},
                },
                headers=auth_a,
            )
        ).json()

        # User B tries to access it
        r_b = await client.post(
            "/v1/auth/register",
            json={"email": "rules-idor-user-b@example.com", "password": "Password1!"},
        )
        auth_b = {"Authorization": f"Bearer {r_b.json()['access_token']}"}

        r = await client.get(f"/v1/rules/{rule['id']}", headers=auth_b)
        assert r.status_code == 404
