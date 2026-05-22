"""Integration tests: entry CRUD, publish/unpublish, viewer access control."""

from __future__ import annotations

from httpx import AsyncClient


async def _setup(client: AsyncClient, email: str = "entry@example.com") -> tuple[str, dict]:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return token, diary


class TestCreateEntry:
    async def test_create_manual_entry(self, client):
        token, diary = await _setup(client, "ce@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/entries",
            json={"entry_date": "2025-06-01", "title": "Day 1", "body_markdown": "Nice day."},
            headers=auth,
        )
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "draft"
        assert data["created_by"] == "manual"
        assert data["diary_id"] == diary["id"]

    async def test_missing_entry_date_rejected(self, client):
        token, diary = await _setup(client, "ced@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post(
            f"/v1/diaries/{diary['id']}/entries",
            json={"title": "No date"},
            headers=auth,
        )
        assert r.status_code == 422


class TestListEntries:
    async def test_list_returns_entries(self, client):
        token, diary = await _setup(client, "le@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        await client.post(
            f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2025-06-01"}, headers=auth
        )
        await client.post(
            f"/v1/diaries/{diary['id']}/entries", json={"entry_date": "2025-06-02"}, headers=auth
        )
        r = await client.get(f"/v1/diaries/{diary['id']}/entries", headers=auth)
        assert r.status_code == 200
        assert len(r.json()) == 2

    async def test_status_filter(self, client):
        token, diary = await _setup(client, "sf@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers=auth,
            )
        ).json()
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=auth)

        drafts = await client.get(f"/v1/diaries/{diary['id']}/entries?status=draft", headers=auth)
        assert len(drafts.json()) == 0

        published = await client.get(
            f"/v1/diaries/{diary['id']}/entries?status=published", headers=auth
        )
        assert len(published.json()) == 1


class TestPatchEntry:
    async def test_patch_title_and_body(self, client):
        token, diary = await _setup(client, "pe@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers=auth,
            )
        ).json()
        r = await client.patch(
            f"/v1/entries/{entry['id']}",
            json={"title": "Updated", "body_markdown": "New body."},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Updated"
        assert r.json()["body_markdown"] == "New body."


class TestPublishUnpublish:
    async def test_publish_sets_status_and_timestamp(self, client):
        token, diary = await _setup(client, "pub@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers=auth,
            )
        ).json()
        r = await client.post(f"/v1/entries/{entry['id']}/publish", headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "published"
        assert r.json()["published_at"] is not None

    async def test_unpublish_returns_to_draft(self, client):
        token, diary = await _setup(client, "unp@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers=auth,
            )
        ).json()
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=auth)
        r = await client.post(f"/v1/entries/{entry['id']}/unpublish", headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "draft"
        assert r.json()["published_at"] is None

    async def test_idempotent_publish(self, client):
        token, diary = await _setup(client, "idem@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers=auth,
            )
        ).json()
        await client.post(f"/v1/entries/{entry['id']}/publish", headers=auth)
        r = await client.post(f"/v1/entries/{entry['id']}/publish", headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "published"


class TestViewerAccessControl:
    async def test_viewer_cannot_see_drafts(self, client):
        owner_token, diary = await _setup(client, "viewowner@example.com")
        # Viewer: register a second user
        viewer_r = await client.post(
            "/v1/auth/register",
            json={"email": "viewer@example.com", "password": "Password1!"},
        )
        viewer_token = viewer_r.json()["access_token"]

        # Create a draft entry as owner
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers={"Authorization": f"Bearer {owner_token}"},
            )
        ).json()

        # Viewer should get 404 for draft
        r = await client.get(
            f"/v1/entries/{entry['id']}",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert r.status_code == 404
