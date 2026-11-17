"""ai_status on entries and the per-diary status endpoint (row 38)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import DiaryPermission, User
from app.services.ai_draft_jobs import add_job, mark_started
from tests.fixtures.factories import make_diary, make_entry, make_user


@pytest.fixture(autouse=True)
def _wired(client: AsyncClient):
    """Point the worker's db_session() at the test database."""


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _queued(db: AsyncSession, entry) -> None:
    await add_job(db, entry.id)
    await db.commit()


async def _writing(db: AsyncSession, entry) -> None:
    job_id = await add_job(db, entry.id)
    await mark_started(db, job_id, entry.id)
    await db.commit()


async def test_list_reports_each_entry_status(client: AsyncClient, db_session: AsyncSession):
    owner = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    q = await make_entry(db_session, diary)
    w = await make_entry(db_session, diary)
    n = await make_entry(db_session, diary)
    await _queued(db_session, q)
    await _writing(db_session, w)
    r = await client.get(f"/v1/diaries/{diary.id}/entries", headers=_auth(owner))
    assert r.status_code == 200
    got = {e["id"]: e["ai_status"] for e in r.json()}
    assert got == {str(q.id): "queued", str(w.id): "writing", str(n.id): None}


async def test_get_reports_writing_with_overlapping_jobs(
    client: AsyncClient, db_session: AsyncSession
):
    owner = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    e = await make_entry(db_session, diary)
    await _writing(db_session, e)
    await _queued(db_session, e)
    r = await client.get(f"/v1/entries/{e.id}", headers=_auth(owner))
    assert r.status_code == 200
    assert r.json()["ai_status"] == "writing"


async def test_get_without_job_is_null(client: AsyncClient, db_session: AsyncSession):
    owner = await make_user(db_session)
    e = await make_entry(db_session, await make_diary(db_session, owner))
    r = await client.get(f"/v1/entries/{e.id}", headers=_auth(owner))
    assert r.json()["ai_status"] is None


async def test_regenerate_returns_queued(client: AsyncClient, db_session: AsyncSession):
    owner = await make_user(db_session)
    e = await make_entry(db_session, await make_diary(db_session, owner))
    with patch("app.workers.tasks.generate_entry_draft"):
        r = await client.post(f"/v1/entries/{e.id}/regenerate", headers=_auth(owner))
    assert r.status_code == 200, r.text
    assert r.json()["ai_status"] == "queued"


async def test_status_endpoint_owner(client: AsyncClient, db_session: AsyncSession):
    owner = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    other = await make_diary(db_session, await make_user(db_session))
    q = await make_entry(db_session, diary)
    w = await make_entry(db_session, diary)
    await make_entry(db_session, diary)  # no job
    elsewhere = await make_entry(db_session, other)
    await _queued(db_session, q)
    await _writing(db_session, w)
    await _queued(db_session, elsewhere)
    r = await client.get(f"/v1/diaries/{diary.id}/entries/ai-status", headers=_auth(owner))
    assert r.status_code == 200
    assert r.json() == {"entries": {str(q.id): "queued", str(w.id): "writing"}}


async def test_status_endpoint_viewer_sees_published_only(
    client: AsyncClient, db_session: AsyncSession
):
    owner = await make_user(db_session)
    viewer = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    db_session.add(DiaryPermission(diary_id=diary.id, user_id=viewer.id, role="viewer"))
    draft = await make_entry(db_session, diary, status="draft")
    pub = await make_entry(db_session, diary, status="published")
    await _queued(db_session, draft)
    await _queued(db_session, pub)
    r = await client.get(f"/v1/diaries/{diary.id}/entries/ai-status", headers=_auth(viewer))
    assert r.status_code == 200
    assert r.json() == {"entries": {str(pub.id): "queued"}}


async def test_status_endpoint_excludes_trashed(client: AsyncClient, db_session: AsyncSession):
    owner = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    e = await make_entry(db_session, diary)
    await _queued(db_session, e)
    e.deleted_at = datetime.now(UTC)
    await db_session.commit()
    r = await client.get(f"/v1/diaries/{diary.id}/entries/ai-status", headers=_auth(owner))
    assert r.json() == {"entries": {}}


async def test_status_endpoint_non_member_404(client: AsyncClient, db_session: AsyncSession):
    owner = await make_user(db_session)
    stranger = await make_user(db_session)
    diary = await make_diary(db_session, owner)
    r = await client.get(f"/v1/diaries/{diary.id}/entries/ai-status", headers=_auth(stranger))
    assert r.status_code == 404
