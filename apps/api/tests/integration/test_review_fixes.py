"""Regression tests for the November 2026 code review (design/code-review-2026-11.md).

Each test names the finding it guards. Every test here was run against the
pre-fix version of the production file and seen to fail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AutoCreationRule,
    BackfillRun,
    Diary,
    Entry,
    EntryPhoto,
    MagicLinkToken,
    OAuthToken,
    Photo,
    RefreshToken,
    ScanRun,
    User,
)
from tests.fixtures.factories import (
    make_diary,
    make_entry,
    make_entry_photo,
    make_event,
    make_photo,
    make_user,
)

PAST = datetime(2020, 1, 1, tzinfo=UTC)
# Stands in for the Google token refresh, which would call Google.
_FRESH_TOKEN = AsyncMock(return_value="tok")


async def _register(client: AsyncClient, email: str) -> dict:
    r = await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _register_with_diary(client: AsyncClient, email: str) -> tuple[dict, dict]:
    auth = await _register(client, email)
    diary = (
        await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=auth)
    ).json()
    return auth, diary


def _visible_to_another_connection(sync_engine, table: str, row_id: str) -> bool:
    """True if a separate database connection can see the row, i.e. it is committed."""
    with sync_engine.connect() as conn:
        found = conn.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE id = :id"),  # noqa: S608 — table is a literal
            {"id": row_id},
        ).first()
    return found is not None


async def _google_token(db: AsyncSession, user_id: uuid.UUID) -> OAuthToken:
    token = OAuthToken(
        user_id=user_id,
        provider="google",
        access_token_ciphertext=b"not-a-real-ciphertext",
        scopes_granted=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    db.add(token)
    await db.commit()
    return token


# ---------------------------------------------------------------------------
# S-2 — stolen refresh-token detection
# ---------------------------------------------------------------------------


class TestRefreshTokenReuse:
    async def test_replaying_a_rotated_token_revokes_its_successor(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.core.security import hash_token

        r = await client.post(
            "/v1/auth/register", json={"email": "reuse@example.com", "password": "Password1!"}
        )
        stolen = r.cookies["refresh_token"]

        # The cookie is Secure, so the http:// test client never sends it on its
        # own; every call passes the token it means to present.
        client.cookies.clear()
        rotated = await client.post(
            "/v1/auth/refresh", headers={"Cookie": f"refresh_token={stolen}"}
        )
        assert rotated.status_code == 200, rotated.text
        successor = rotated.cookies["refresh_token"]
        assert successor != stolen

        # Move the rotation outside the 30-second reuse grace window.
        await db_session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(stolen))
            .values(revoked_at=datetime.now(tz=UTC) - timedelta(minutes=5))
        )
        await db_session.commit()

        client.cookies.clear()
        replay = await client.post(
            "/v1/auth/refresh", headers={"Cookie": f"refresh_token={stolen}"}
        )
        assert replay.status_code == 401
        assert replay.json()["error"]["message"] == "token_reuse_detected"

        client.cookies.clear()
        after = await client.post(
            "/v1/auth/refresh", headers={"Cookie": f"refresh_token={successor}"}
        )
        assert after.status_code == 401, "the whole token family must be revoked after a replay"



# ---------------------------------------------------------------------------
# S-10 — /readyz must not leak exception text
# ---------------------------------------------------------------------------


class TestReadinessErrorDetail:
    async def test_failed_check_does_not_expose_the_exception_message(self, client: AsyncClient):
        boom = RuntimeError("connection to db-internal-host:5432 failed, password=hunter2")
        with patch("app.core.database.get_engine", side_effect=boom):
            r = await client.get("/readyz")

        assert r.status_code == 503
        assert r.json()["checks"]["postgres"] == "error"
        assert "hunter2" not in r.text
        assert "db-internal-host" not in r.text


# ---------------------------------------------------------------------------
# D-1, D-2, D-3 — hard deletes
# ---------------------------------------------------------------------------


class TestHardDeleteScope:
    async def test_deleting_one_user_keeps_another_users_magic_link_token(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.hard_delete import hard_delete_user

        leaving = await make_user(db_session, email="leaving@example.com")
        expires = datetime.now(tz=UTC) + timedelta(minutes=15)
        db_session.add_all(
            [
                MagicLinkToken(email=email, token_hash=f"hash-{email}", expires_at=expires)
                for email in ("leaving@example.com", "staying@example.com")
            ]
        )
        await db_session.commit()

        await hard_delete_user(leaving.id)

        remaining = (await db_session.execute(select(MagicLinkToken.email))).scalars().all()
        assert remaining == ["staying@example.com"]

    async def test_deleting_a_diary_keeps_a_photo_another_diary_still_uses(
        self, client: AsyncClient, db_session: AsyncSession, s3_client, photos_bucket
    ):
        from app.workers.hard_delete import hard_delete_diary

        user = await make_user(db_session)
        doomed = await make_diary(db_session, owner=user, name="Doomed diary")
        kept = await make_diary(db_session, owner=user, name="Kept diary")
        doomed_entry = await make_entry(db_session, doomed)
        kept_entry = await make_entry(db_session, kept)
        shared = await make_photo(db_session, user=user)
        await make_entry_photo(db_session, entry=doomed_entry, photo=shared)
        await make_entry_photo(db_session, entry=kept_entry, photo=shared)
        await db_session.commit()
        s3_client.put_object(Bucket=photos_bucket, Key=shared.s3_key, Body=b"full")
        s3_client.put_object(Bucket=photos_bucket, Key=shared.thumbnail_s3_key, Body=b"thumb")

        await hard_delete_diary(doomed.id)

        photo_ids = (await db_session.execute(select(Photo.id))).scalars().all()
        assert photo_ids == [shared.id]
        links = (await db_session.execute(select(EntryPhoto.entry_id))).scalars().all()
        assert links == [kept_entry.id]
        keys = {
            o["Key"] for o in s3_client.list_objects_v2(Bucket=photos_bucket).get("Contents", [])
        }
        assert {shared.s3_key, shared.thumbnail_s3_key} <= keys

    async def test_deleting_a_diary_removes_a_photo_only_it_uses(
        self, client: AsyncClient, db_session: AsyncSession, s3_client, photos_bucket
    ):
        from app.workers.hard_delete import hard_delete_diary

        user = await make_user(db_session)
        doomed = await make_diary(db_session, owner=user, name="Doomed diary")
        entry = await make_entry(db_session, doomed)
        solo = await make_photo(db_session, user=user)
        await make_entry_photo(db_session, entry=entry, photo=solo)
        await db_session.commit()
        s3_client.put_object(Bucket=photos_bucket, Key=solo.s3_key, Body=b"full")

        await hard_delete_diary(doomed.id)

        assert (await db_session.execute(select(Photo.id))).scalars().all() == []
        keys = {
            o["Key"] for o in s3_client.list_objects_v2(Bucket=photos_bucket).get("Contents", [])
        }
        assert solo.s3_key not in keys

    async def test_one_failing_delete_does_not_block_the_rest_of_the_run(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.beat_tasks import _process_hard_deletes

        owner = await make_user(db_session)
        broken = await make_diary(db_session, owner=owner, name="Broken diary")
        broken.deleted_at = PAST
        broken.hard_delete_after = PAST
        leaving = await make_user(db_session, email="due@example.com")
        leaving.deleted_at = PAST
        leaving.hard_delete_after = PAST
        await db_session.commit()

        with patch(
            "app.workers.hard_delete.hard_delete_diary",
            new=AsyncMock(side_effect=RuntimeError("storage unavailable")),
        ):
            await _process_hard_deletes()

        emails = (await db_session.execute(select(User.email))).scalars().all()
        assert "due@example.com" not in emails, "the user queued behind the failure must still go"
        assert owner.email in emails


# ---------------------------------------------------------------------------
# D-7 — accounts in the deletion grace window
# ---------------------------------------------------------------------------


class TestDeletedAccountIsNotScanned:
    async def test_due_scan_dispatch_skips_a_soft_deleted_owner(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.beat_tasks import _dispatch_due_scans

        active = await make_user(db_session)
        active_diary = await make_diary(db_session, owner=active, name="Active diary")
        leaving = await make_user(db_session)
        await make_diary(db_session, owner=leaving, name="Leaving diary")
        leaving.deleted_at = datetime.now(tz=UTC)
        await db_session.commit()

        with patch("app.workers.tasks.scan_diary") as scan_task:
            await _dispatch_due_scans()

        queued = [call.args[0] for call in scan_task.delay.call_args_list]
        assert queued == [str(active_diary.id)]

    async def test_scan_of_a_soft_deleted_owners_diary_does_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _scan_diary

        leaving = await make_user(db_session)
        diary = await make_diary(db_session, owner=leaving, name="Leaving diary")
        leaving.deleted_at = datetime.now(tz=UTC)
        await db_session.commit()

        await _scan_diary(str(diary.id))

        runs = (await db_session.execute(select(ScanRun.id))).scalars().all()
        assert runs == []

    async def test_deleting_the_account_revokes_the_google_grant(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth = await _register(client, "revoke@example.com")
        user_id = (
            await db_session.execute(select(User.id).where(User.email == "revoke@example.com"))
        ).scalar_one()
        await _google_token(db_session, user_id)

        r = await client.delete("/v1/auth/account", headers=auth)
        assert r.status_code == 204

        revoked_at = (
            await db_session.execute(
                select(OAuthToken.revoked_at).where(OAuthToken.user_id == user_id)
            )
        ).scalar_one()
        assert revoked_at is not None


# ---------------------------------------------------------------------------
# C-4 — a background job is queued only after its row is committed
# ---------------------------------------------------------------------------


class TestJobsQueuedAfterCommit:
    async def test_backfill_run_is_committed_when_the_job_is_queued(
        self, client: AsyncClient, sync_engine
    ):
        auth, diary = await _register_with_diary(client, "c4-backfill@example.com")
        seen: list[bool] = []

        def _on_queue(run_id: str) -> None:
            seen.append(_visible_to_another_connection(sync_engine, "backfill_runs", run_id))

        with patch("app.workers.tasks.backfill_diary.delay", side_effect=_on_queue):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/scan/backfill",
                json={"from_date": "2026-05-01", "to_date": "2026-05-15"},
                headers=auth,
            )

        assert r.status_code == 202, r.text
        assert seen == [True]

    async def test_entry_from_event_is_committed_when_the_draft_is_queued(
        self, client: AsyncClient, db_session: AsyncSession, sync_engine
    ):
        auth, diary = await _register_with_diary(client, "c4-event@example.com")
        event = await make_event(db_session, diary_id=uuid.UUID(diary["id"]))
        seen: list[bool] = []

        def _on_queue(entry_id: str) -> None:
            seen.append(_visible_to_another_connection(sync_engine, "entries", entry_id))

        with patch("app.routers.v1.calendar_events.generate_entry_draft") as draft_task:
            draft_task.delay = MagicMock(side_effect=_on_queue)
            r = await client.post(
                f"/v1/diaries/{diary['id']}/entries/from-event",
                json={"event_id": str(event.id)},
                headers=auth,
            )

        assert r.status_code == 201, r.text
        assert seen == [True]

    async def test_rule_created_entry_is_committed_when_the_draft_is_queued(
        self, client: AsyncClient, db_session: AsyncSession, sync_engine
    ):
        from app.workers.rules import evaluate_event_against_rules

        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user, timezone="UTC")
        db_session.add(
            AutoCreationRule(
                diary_id=diary.id,
                name="Soccer",
                enabled=True,
                condition={
                    "op": "AND",
                    "children": [
                        {
                            "field": "title",
                            "op": "contains",
                            "value": "Soccer",
                            "case_sensitive": False,
                        }
                    ],
                },
                options={"recurring": "per_instance", "multi_day": "per_day"},
            )
        )
        await db_session.commit()
        event = await make_event(
            db_session,
            diary_id=diary.id,
            payload={
                "summary": "Soccer practice",
                "start": {"dateTime": "2026-06-01T10:00:00Z"},
                "end": {},
                "attendees": [],
                "description": "",
                "location": "",
                "status": "",
            },
        )
        seen: list[bool] = []

        def _on_queue(entry_id: str) -> None:
            seen.append(_visible_to_another_connection(sync_engine, "entries", entry_id))

        with patch("app.workers.tasks.generate_entry_draft") as draft_task:
            draft_task.delay = MagicMock(side_effect=_on_queue)
            await evaluate_event_against_rules(str(event.id), str(diary.id), db_session)

        assert seen == [True]


# ---------------------------------------------------------------------------
# C-7 — entries beyond the first page are reachable
# ---------------------------------------------------------------------------


class TestEntryPaging:
    async def test_cursor_walks_the_whole_timeline_newest_first(self, client: AsyncClient):
        auth, diary = await _register_with_diary(client, "paging@example.com")
        for day in ("2025-06-01", "2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05"):
            r = await client.post(
                f"/v1/diaries/{diary['id']}/entries", json={"entry_date": day}, headers=auth
            )
            assert r.status_code == 201, r.text

        url = f"/v1/diaries/{diary['id']}/entries?limit=2"
        page1 = (await client.get(url, headers=auth)).json()
        page2 = (await client.get(f"{url}&cursor={page1[-1]['id']}", headers=auth)).json()
        page3 = (await client.get(f"{url}&cursor={page2[-1]['id']}", headers=auth)).json()
        page4 = (await client.get(f"{url}&cursor={page3[-1]['id']}", headers=auth)).json()

        assert [e["entry_date"] for e in page1] == ["2025-06-05", "2025-06-04"]
        assert [e["entry_date"] for e in page2] == ["2025-06-03", "2025-06-02"]
        assert [e["entry_date"] for e in page3] == ["2025-06-01"]
        assert page4 == []

    async def test_entries_with_identical_date_and_timestamp_are_each_returned_once(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth, diary = await _register_with_diary(client, "ties@example.com")
        same_moment = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
        ids = []
        for _ in range(3):
            entry = Entry(
                diary_id=uuid.UUID(diary["id"]),
                entry_date=date(2025, 6, 1),
                status="draft",
                created_by="manual",
                created_at=same_moment,
            )
            db_session.add(entry)
            await db_session.flush()
            ids.append(str(entry.id))
        await db_session.commit()

        url = f"/v1/diaries/{diary['id']}/entries?limit=1"
        seen = []
        cursor = ""
        for _ in range(3):
            page = (await client.get(f"{url}{cursor}", headers=auth)).json()
            assert len(page) == 1
            seen.append(page[0]["id"])
            cursor = f"&cursor={page[0]['id']}"

        assert sorted(seen) == sorted(ids)

    async def test_cursor_from_another_diary_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        auth, diary = await _register_with_diary(client, "badcursor@example.com")
        stranger = await make_user(db_session)
        other_diary = await make_diary(db_session, owner=stranger, name="Someone else")
        foreign_entry = await make_entry(db_session, other_diary)

        r = await client.get(
            f"/v1/diaries/{diary['id']}/entries?cursor={foreign_entry.id}", headers=auth
        )

        assert r.status_code == 400
        assert r.json()["error"]["message"] == "invalid_cursor"


# ---------------------------------------------------------------------------
# C-11 — a cancelled backfill stays cancelled
# ---------------------------------------------------------------------------


class TestBackfillCancellation:
    async def _run(self, db: AsyncSession, diary: Diary, status: str) -> BackfillRun:
        run = BackfillRun(
            diary_id=diary.id,
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 8),
            sources=["google_calendar"],
            status=status,
        )
        db.add(run)
        await db.commit()
        return run

    async def _status(self, db: AsyncSession, run_id: uuid.UUID) -> str:
        return (
            await db.execute(select(BackfillRun.status).where(BackfillRun.id == run_id))
        ).scalar_one()

    async def test_run_cancelled_before_the_worker_starts_is_left_alone(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.workers.tasks import _backfill_diary

        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)
        await _google_token(db_session, user.id)
        run = await self._run(db_session, diary, "cancelled")
        fetch = AsyncMock(return_value=(0, 0))

        with (
            patch("app.workers.token_refresh.ensure_fresh_access_token", _FRESH_TOKEN),
            patch("app.workers.backfill.run_backfill", fetch),
        ):
            await _backfill_diary(str(run.id))

        assert await self._status(db_session, run.id) == "cancelled"
        assert fetch.await_count == 0, "a cancelled run must not fetch anything"

    async def test_run_cancelled_while_fetching_is_not_marked_completed(
        self, client: AsyncClient, db_session: AsyncSession, sync_engine
    ):
        from app.workers.tasks import _backfill_diary

        user = await make_user(db_session)
        diary = await make_diary(db_session, owner=user)
        await _google_token(db_session, user.id)
        run = await self._run(db_session, diary, "pending")

        async def _cancelled_mid_flight(**_kwargs):
            # What DELETE /scan/backfill/{id} does while the worker is fetching.
            with sync_engine.connect() as conn:
                conn.execute(
                    sa.text("UPDATE backfill_runs SET status = 'cancelled' WHERE id = :id"),
                    {"id": str(run.id)},
                )
                conn.commit()
            return (3, 1)

        with (
            patch("app.workers.token_refresh.ensure_fresh_access_token", _FRESH_TOKEN),
            patch("app.workers.backfill.run_backfill", side_effect=_cancelled_mid_flight),
        ):
            await _backfill_diary(str(run.id))

        assert await self._status(db_session, run.id) == "cancelled"
