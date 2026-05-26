"""Integration tests: entry tier limit enforcement."""

from __future__ import annotations

from httpx import AsyncClient


async def _setup(client: AsyncClient, email: str) -> tuple[str, dict]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, diary


async def _create_entry(client: AsyncClient, auth: dict, diary_id: str, day: int) -> int:
    r = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": f"2025-06-{day:02d}"},
        headers=auth,
    )
    return r.status_code


class TestEntryTierLimits:
    async def test_free_tier_manual_limit_enforced(self, client: AsyncClient):
        """Free tier allows 5 manual entries, 6th returns 403 with structured detail."""
        token, diary = await _setup(client, "tier-manual@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary_id = diary["id"]

        for day in range(1, 6):
            status = await _create_entry(client, auth, diary_id, day)
            assert status == 201, f"Entry {day} should succeed (got {status})"

        # 6th entry should be rejected
        r = await client.post(
            f"/v1/diaries/{diary_id}/entries",
            json={"entry_date": "2025-06-10"},
            headers=auth,
        )
        assert r.status_code == 403
        body = r.json()
        detail = body.get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "tier_limit"
        inner = detail.get("details", {})
        assert inner.get("source") == "manual"
        assert inner.get("limit") == 5
        assert inner.get("current") == 5
        assert inner.get("required_tier") == "tier1"

    async def test_free_tier_allows_exactly_5_entries(self, client: AsyncClient):
        """Free tier: 5th entry succeeds, counting is exact."""
        token, diary = await _setup(client, "tier-exact@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary_id = diary["id"]

        for day in range(1, 5):
            await _create_entry(client, auth, diary_id, day)

        # 5th should succeed
        status = await _create_entry(client, auth, diary_id, 5)
        assert status == 201

    async def test_deleted_entries_not_counted(self, client: AsyncClient):
        """Soft-deleted entries don't count toward the limit."""
        token, diary = await _setup(client, "tier-delete@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        diary_id = diary["id"]

        # Create 5 entries
        entry_ids = []
        for day in range(1, 6):
            r = await client.post(
                f"/v1/diaries/{diary_id}/entries",
                json={"entry_date": f"2025-06-{day:02d}"},
                headers=auth,
            )
            entry_ids.append(r.json()["id"])

        # Delete one
        await client.delete(f"/v1/entries/{entry_ids[0]}", headers=auth)

        # Should be able to create another (only 4 active)
        r = await client.post(
            f"/v1/diaries/{diary_id}/entries",
            json={"entry_date": "2025-06-10"},
            headers=auth,
        )
        assert r.status_code == 201
