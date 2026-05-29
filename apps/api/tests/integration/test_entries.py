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

    async def test_patch_entry_date_and_end_date(self, client):
        token, diary = await _setup(client, "pdate@example.com")
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
            json={"entry_date": "2025-06-10", "entry_end_date": "2025-06-12"},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["entry_date"] == "2025-06-10"
        assert r.json()["entry_end_date"] == "2025-06-12"

    async def test_patch_clear_end_date_with_null(self, client):
        token, diary = await _setup(client, "pclear@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01", "entry_end_date": "2025-06-03"},
                headers=auth,
            )
        ).json()
        assert entry["entry_end_date"] == "2025-06-03"

        r = await client.patch(
            f"/v1/entries/{entry['id']}",
            json={"entry_end_date": None},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["entry_end_date"] is None
        # Start date untouched.
        assert r.json()["entry_date"] == "2025-06-01"

    async def test_patch_omitting_field_leaves_it_unchanged(self, client):
        token, diary = await _setup(client, "pomit@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01", "entry_end_date": "2025-06-03"},
                headers=auth,
            )
        ).json()
        # Patch only the title — end date must NOT be cleared.
        r = await client.patch(
            f"/v1/entries/{entry['id']}",
            json={"title": "Trip"},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Trip"
        assert r.json()["entry_end_date"] == "2025-06-03"


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


async def test_get_entry_exposes_enrichments(client, db_session):
    """Entry detail GET returns weather enrichments list including nullable source."""
    import uuid as _uuid
    from datetime import UTC, datetime
    from app.models import Enrichment

    email = f"enrich-{_uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Create diary + entry via HTTP API
    diary_r = await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    diary_id = diary_r.json()["id"]

    entry_r = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": "2026-05-05", "title": "Test", "body_markdown": ""},
        headers=auth,
    )
    assert entry_r.status_code == 201
    entry_id = entry_r.json()["id"]

    # Seed two enrichments via db_session (worker-written; no public API)
    enrich1 = Enrichment(
        entry_id=_uuid.UUID(entry_id),
        kind="weather",
        source="open_meteo",
        payload={
            "date": "2026-05-05",
            "temperature_max_c": 22.0,
            "temperature_min_c": 14.0,
            "weathercode": 1,
            "condition": "mainly clear",
        },
        captured_for_at=datetime(2026, 5, 5, tzinfo=UTC),
        fetched_at=datetime.now(UTC),
    )
    enrich2 = Enrichment(
        entry_id=_uuid.UUID(entry_id),
        kind="weather",
        source=None,  # test nullable source
        payload={"date": "2026-05-06", "temperature_max_c": 18.0, "temperature_min_c": 12.0, "weathercode": 2},
        captured_for_at=datetime(2026, 5, 6, tzinfo=UTC),
        fetched_at=datetime.now(UTC),
    )
    db_session.add(enrich1)
    db_session.add(enrich2)
    await db_session.commit()

    r = await client.get(f"/v1/entries/{entry_id}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "enrichments" in body
    assert len(body["enrichments"]) == 2

    # Find the enrichment with source="open_meteo"
    e = next(x for x in body["enrichments"] if x["source"] == "open_meteo")
    assert e["kind"] == "weather"
    assert e["payload"]["weathercode"] == 1
    assert e["payload"]["temperature_max_c"] == 22.0
    assert "captured_for_at" in e
    assert e["source"] == "open_meteo"

    # Verify nullable source serialises as null
    e2 = next(x for x in body["enrichments"] if x["source"] is None)
    assert e2["source"] is None
