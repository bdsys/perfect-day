"""Task 7: the hand-over and the photo moves at the hard delete.

Runs against the real Garage container through the `client` fixture, as
test_hard_delete_photos.py does: copy_object is exercised for real.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

import pytest
import structlog
from sqlalchemy import delete, select, update
from structlog.testing import capture_logs

from app.models import (
    AuditLog,
    BackfillRun,
    Diary,
    DiaryCalendarFilter,
    DiaryPermission,
    Entry,
    EntryPhoto,
    Event,
    Invitation,
    Photo,
    ScanJob,
    User,
)
from tests.integration.photo_helpers import jpeg_with_exif, upload_photo
from tests.integration.sharing_helpers import accept, invite, register, set_tier

ACCOUNT = "/v1/auth/account"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _user(db_session, email: str) -> User:
    return (
        await db_session.execute(
            select(User).where(User.email == email).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _diary(db_session, diary_id) -> Diary | None:
    return (
        await db_session.execute(
            select(Diary)
            .where(Diary.id == uuid.UUID(str(diary_id)))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _photo(db_session, photo_id) -> Photo | None:
    return (
        await db_session.execute(
            select(Photo)
            .where(Photo.id == uuid.UUID(str(photo_id)))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _new_diary(client, headers, name: str = "Test Diary") -> str:
    r = await client.post("/v1/diaries", json={"name": name, "timezone": "UTC"}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _owner_with_diary(client, email: str, tier: str = "free", name: str = "Test Diary"):
    headers = await register(client, email)
    await set_tier(email, tier)
    return headers, await _new_diary(client, headers, name)


async def _add_member(client, owner_headers, did: str, email: str, role: str = "viewer") -> dict:
    created = await invite(client, owner_headers, did, email, role)
    headers = await register(client, email)
    assert (await accept(client, headers, created["token"])).status_code == 200
    return headers


async def _entry_with_photo(client, headers, did: str, photo_id: str, publish: bool = True) -> str:
    r = await client.post(
        f"/v1/diaries/{did}/entries",
        json={"entry_date": date.today().isoformat(), "title": "Park", "body_markdown": "Swings."},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]
    r = await client.post(
        f"/v1/entries/{entry_id}/photos", json={"photo_id": photo_id}, headers=headers
    )
    assert r.status_code == 201, r.text
    if publish:
        r = await client.post(f"/v1/entries/{entry_id}/publish", headers=headers)
        assert r.status_code == 200, r.text
    return entry_id


async def _delete_account(client, headers, handovers: dict[str, uuid.UUID] | None = None):
    if handovers:
        body = {"handovers": {d: str(u) for d, u in handovers.items()}}
        r = await client.request("DELETE", ACCOUNT, json=body, headers=headers)
    else:
        r = await client.delete(ACCOUNT, headers=headers)
    assert r.status_code == 200, r.text


async def _make_due(db_session, email: str) -> uuid.UUID:
    from tests.fixtures.factories import mark_due_for_hard_delete

    user = await _user(db_session, email)
    await mark_due_for_hard_delete(db_session, user)
    return user.id


def _keys(s3_client, bucket: str, prefix: str) -> set[str]:
    listed = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return {o["Key"] for o in listed.get("Contents", [])}


def _storage_down():
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "down"}}, "CopyObject")


# ---------------------------------------------------------------------------
# The hand-over itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handover_gives_diary_and_photos_to_recipient(
    client, db_session, s3_client, photos_bucket
):
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7a@example.com")
    m_headers = await _add_member(client, owner, did, "t7a-m@example.com")
    await _add_member(client, owner, did, "t7a-o@example.com")
    await invite(client, owner, did, "t7a-pending@example.com")  # never accepted
    original = jpeg_with_exif()
    pid = await upload_photo(client, owner, original)
    await _entry_with_photo(client, owner, did, pid)
    m = await _user(db_session, "t7a-m@example.com")
    other = await _user(db_session, "t7a-o@example.com")

    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7a@example.com")

    await hard_delete_user(owner_id)

    diary = await _diary(db_session, did)
    assert diary is not None
    assert diary.owner_user_id == m.id
    assert diary.handover_to_user_id is None
    perms = {
        p.user_id: p
        for p in (
            await db_session.execute(
                select(DiaryPermission).where(DiaryPermission.diary_id == diary.id)
            )
        ).scalars()
    }
    assert m.id not in perms
    assert other.id in perms
    unaccepted = (
        await db_session.execute(
            select(Invitation.id).where(
                Invitation.diary_id == diary.id, Invitation.accepted_at.is_(None)
            )
        )
    ).all()
    assert unaccepted == []
    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "diary.handover", AuditLog.target_id == diary.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert audit[0].target_type == "diary"

    r = await client.get(f"/v1/photos/{pid}", params={"kind": "original"}, headers=m_headers)
    assert r.status_code == 200, r.text
    assert r.content == original

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == m.id
    expected = {k for k in (photo.s3_key, photo.thumbnail_s3_key, photo.display_s3_key) if k}
    assert all(k.startswith(f"{m.id}/") for k in expected)
    assert expected <= _keys(s3_client, photos_bucket, f"{m.id}/")
    assert _keys(s3_client, photos_bucket, f"{owner_id}/") == set()
    assert (await db_session.execute(select(User.id).where(User.id == owner_id))).first() is None


@pytest.mark.parametrize("how", ["left", "removed", "pending_deletion", "declined"])
@pytest.mark.asyncio
async def test_recipient_unable_means_diary_deleted(client, db_session, how):
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, f"t7b-{how}@example.com")
    m_email = f"t7b-{how}-m@example.com"
    m_headers = await _add_member(client, owner, did, m_email)
    m = await _user(db_session, m_email)
    await _delete_account(client, owner, {did: m.id})

    if how == "left":
        r = await client.delete(f"/v1/diaries/{did}/permissions/{m.id}", headers=m_headers)
        assert r.status_code == 204, r.text
    elif how == "removed":
        # The permission row goes without the choice being cleared: the race with
        # remove_member the hard delete has to re-check under the diary lock.
        await db_session.execute(
            delete(DiaryPermission).where(
                DiaryPermission.diary_id == uuid.UUID(did), DiaryPermission.user_id == m.id
            )
        )
        await db_session.commit()
    elif how == "pending_deletion":
        await _delete_account(client, m_headers)
    else:
        r = await client.post(f"/v1/diaries/{did}/handover/decline", headers=m_headers)
        assert r.status_code == 204, r.text

    owner_id = await _make_due(db_session, f"t7b-{how}@example.com")
    # The app caches loggers on first use (app/core/logging.py): bind a fresh one
    # inside the capture, as test_event_edit_redraft.py does.
    with capture_logs() as logs, patch("app.workers.handover.log", structlog.get_logger()):
        await hard_delete_user(owner_id)

    assert await _diary(db_session, did) is None
    events = [e["event"] for e in logs]
    assert "handover_diary_transferred" not in events
    skipped = [e for e in logs if e["event"] == "handover_skipped"]
    if how == "removed":
        assert [(e["diary_id"], e["reason"]) for e in skipped] == [(did, "not_member")]
    elif how == "pending_deletion":
        assert [(e["diary_id"], e["reason"]) for e in skipped] == [(did, "recipient_not_active")]
    else:  # leaving and declining clear the choice themselves
        assert skipped == []
    assert (await db_session.execute(select(User.id).where(User.id == owner_id))).first() is None


@pytest.mark.asyncio
async def test_received_diary_slug_clash(client, db_session):
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7c@example.com", name="Test Diary")
    m_headers = await _add_member(client, owner, did, "t7c-m@example.com")
    own_id = await _new_diary(client, m_headers, "Test Diary")
    assert (await _diary(db_session, own_id)).slug == "test-diary"
    m = await _user(db_session, "t7c-m@example.com")
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7c@example.com")

    await hard_delete_user(owner_id)

    received = await _diary(db_session, did)
    assert received is not None and received.owner_user_id == m.id
    assert received.slug == "test-diary-1"


@pytest.mark.asyncio
async def test_received_diary_over_limit_is_read_only(client, db_session):
    from app.services.read_only import read_only_diary_ids
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7d@example.com")
    m_headers = await _add_member(client, owner, did, "t7d-m@example.com")
    await _new_diary(client, m_headers, "Mine")
    m = await _user(db_session, "t7d-m@example.com")
    assert m.subscription_tier == "free"
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7d@example.com")

    await hard_delete_user(owner_id)

    assert await read_only_diary_ids(db_session, m.id, "free") == {uuid.UUID(did)}


@pytest.mark.asyncio
async def test_received_diary_scan_off_and_calendar_state_cleared(client, db_session):
    from datetime import UTC, datetime

    from app.workers.hard_delete import hard_delete_user
    from tests.fixtures.factories import make_event

    owner, did = await _owner_with_diary(client, "t7e@example.com")
    await _add_member(client, owner, did, "t7e-m@example.com")
    m = await _user(db_session, "t7e-m@example.com")
    diary_uuid = uuid.UUID(did)
    r = await client.post(
        f"/v1/diaries/{did}/entries",
        json={"entry_date": date.today().isoformat(), "title": "Zoo"},
        headers=owner,
    )
    assert r.status_code == 201, r.text
    entry = (
        await db_session.execute(select(Entry).where(Entry.id == uuid.UUID(r.json()["id"])))
    ).scalar_one()
    attached = await make_event(db_session, entry=entry)
    unattached = await make_event(db_session, diary_id=diary_uuid)
    db_session.add(DiaryCalendarFilter(diary_id=diary_uuid, google_calendar_id="owner@cal"))
    await db_session.execute(
        update(ScanJob)
        .where(ScanJob.diary_id == diary_uuid)
        .values(
            last_calendar_cursor="cal-cursor",
            last_photos_cursor="photos-cursor",
            next_scan_after=datetime.now(tz=UTC),
            consecutive_failures=3,
        )
    )
    backfill = BackfillRun(
        diary_id=diary_uuid,
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 31),
        sources=["google_calendar"],
        status="pending",
    )
    db_session.add(backfill)
    await db_session.commit()
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7e@example.com")

    await hard_delete_user(owner_id)

    diary = await _diary(db_session, did)
    assert diary is not None and diary.owner_user_id == m.id
    assert diary.scan_enabled is False
    filters = (
        await db_session.execute(
            select(DiaryCalendarFilter.id).where(DiaryCalendarFilter.diary_id == diary_uuid)
        )
    ).all()
    assert filters == []
    event_ids = set(
        (await db_session.execute(select(Event.id).where(Event.diary_id == diary_uuid))).scalars()
    )
    assert attached.id in event_ids
    assert unattached.id not in event_ids
    job = (
        await db_session.execute(
            select(ScanJob)
            .where(ScanJob.diary_id == diary_uuid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert job.last_calendar_cursor is None
    assert job.last_photos_cursor is None
    assert job.next_scan_after is None
    assert job.consecutive_failures == 0
    await db_session.refresh(backfill)
    assert backfill.status == "cancelled"


# ---------------------------------------------------------------------------
# Photo moves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editor_photos_stay_in_owners_diary(client, db_session, s3_client, photos_bucket):
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7f@example.com", tier="family")
    editor = await _add_member(client, owner, did, "t7f-e@example.com", role="editor")
    original = jpeg_with_exif()
    pid = await upload_photo(client, editor, original)
    await _entry_with_photo(client, editor, did, pid, publish=False)
    owner_user = await _user(db_session, "t7f@example.com")

    await _delete_account(client, editor)
    editor_id = await _make_due(db_session, "t7f-e@example.com")
    await hard_delete_user(editor_id)

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_user.id
    r = await client.get(f"/v1/photos/{pid}", params={"kind": "original"}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.content == original
    assert _keys(s3_client, photos_bucket, f"{editor_id}/") == set()
    assert (await db_session.execute(select(User.id).where(User.id == editor_id))).first() is None


@pytest.mark.asyncio
async def test_photo_in_handed_and_deleted_diary_survives(
    client, db_session, s3_client, photos_bucket
):
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7g@example.com", tier="plus")
    m_headers = await _add_member(client, owner, did, "t7g-m@example.com")
    solo = await _new_diary(client, owner, "Solo")
    original = jpeg_with_exif()
    pid = await upload_photo(client, owner, original)
    await _entry_with_photo(client, owner, solo, pid)  # the owner's own diary, attached first
    await _entry_with_photo(client, owner, did, pid)
    m = await _user(db_session, "t7g-m@example.com")
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7g@example.com")

    await hard_delete_user(owner_id)

    assert await _diary(db_session, solo) is None
    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == m.id
    r = await client.get(f"/v1/photos/{pid}", params={"kind": "original"}, headers=m_headers)
    assert r.status_code == 200, r.text
    assert r.content == original
    assert _keys(s3_client, photos_bucket, f"{owner_id}/") == set()


@pytest.mark.asyncio
async def test_google_photos_clash_resolves(client, db_session):
    from app.workers.hard_delete import hard_delete_user
    from tests.fixtures.factories import make_entry, make_entry_photo, make_photo

    owner, did = await _owner_with_diary(client, "t7h@example.com")
    await _add_member(client, owner, did, "t7h-m@example.com")
    owner_user = await _user(db_session, "t7h@example.com")
    m = await _user(db_session, "t7h-m@example.com")
    diary = await _diary(db_session, did)
    mine = await make_photo(db_session, user=m, source="google_photos")
    theirs = await make_photo(db_session, user=owner_user, source="google_photos")
    mine.external_id = theirs.external_id = "g1"
    await db_session.commit()
    only_theirs = await make_entry(db_session, diary, title="Only theirs")
    both = await make_entry(db_session, diary, title="Both")
    await make_entry_photo(db_session, entry=only_theirs, photo=theirs)
    await make_entry_photo(db_session, entry=both, photo=theirs)
    await make_entry_photo(db_session, entry=both, photo=mine)
    await db_session.commit()
    mine_id, theirs_id = mine.id, theirs.id
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7h@example.com")

    await hard_delete_user(owner_id)

    rows = (
        await db_session.execute(
            select(Photo.id, Photo.user_id)
            .where(Photo.external_id == "g1")
            .execution_options(populate_existing=True)
        )
    ).all()
    assert rows == [(mine_id, m.id)]
    links = set(
        (
            await db_session.execute(
                select(EntryPhoto.entry_id, EntryPhoto.photo_id).where(
                    EntryPhoto.entry_id.in_([only_theirs.id, both.id])
                )
            )
        ).all()
    )
    assert links == {(only_theirs.id, mine_id), (both.id, mine_id)}
    assert theirs_id not in {p for _, p in links}
    assert (await db_session.execute(select(User.id).where(User.id == owner_id))).first() is None


@pytest.mark.asyncio
async def test_move_retry_after_storage_failure(client, db_session, s3_client, photos_bucket):
    from botocore.exceptions import ClientError

    from app.core.dependencies import get_s3
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7i@example.com")
    m_headers = await _add_member(client, owner, did, "t7i-m@example.com")
    original = jpeg_with_exif()
    pid = await upload_photo(client, owner, original)
    await _entry_with_photo(client, owner, did, pid)
    m = await _user(db_session, "t7i-m@example.com")
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7i@example.com")

    with patch.object(get_s3(), "copy_object", side_effect=_storage_down()):
        with pytest.raises(ClientError):
            await hard_delete_user(owner_id)

    assert (await db_session.execute(select(User.id).where(User.id == owner_id))).first()
    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_id

    await hard_delete_user(owner_id)  # storage is back: the next run finishes it

    assert (await db_session.execute(select(User.id).where(User.id == owner_id))).first() is None
    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == m.id
    r = await client.get(f"/v1/photos/{pid}", params={"kind": "original"}, headers=m_headers)
    assert r.status_code == 200, r.text
    assert r.content == original
    assert _keys(s3_client, photos_bucket, f"{owner_id}/") == set()


@pytest.mark.asyncio
async def test_move_retry_after_crash_between_copy_and_commit(
    client, db_session, s3_client, photos_bucket
):
    """The copies landed but the row update never committed: the next run copies again."""
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7j@example.com")
    m_headers = await _add_member(client, owner, did, "t7j-m@example.com")
    original = jpeg_with_exif()
    pid = await upload_photo(client, owner, original)
    await _entry_with_photo(client, owner, did, pid)
    m = await _user(db_session, "t7j-m@example.com")
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7j@example.com")

    with patch("app.workers.handover.wrap_dek", side_effect=RuntimeError("crash")):
        with pytest.raises(RuntimeError):
            await hard_delete_user(owner_id)

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_id
    assert _keys(s3_client, photos_bucket, f"{m.id}/")  # copied, not yet pointed at
    assert _keys(s3_client, photos_bucket, f"{owner_id}/")  # originals untouched

    await hard_delete_user(owner_id)

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == m.id
    r = await client.get(f"/v1/photos/{pid}", params={"kind": "original"}, headers=m_headers)
    assert r.status_code == 200, r.text
    assert r.content == original
    assert _keys(s3_client, photos_bucket, f"{owner_id}/") == set()


@pytest.mark.asyncio
async def test_missing_objects_do_not_block_the_delete(
    client, db_session, s3_client, photos_bucket
):
    """An object already gone is not an outage: retrying would never finish the delete."""
    from app.workers.hard_delete import hard_delete_user
    from tests.fixtures.factories import make_entry, make_entry_photo, make_photo

    owner, did = await _owner_with_diary(client, "t7k@example.com")
    m_headers = await _add_member(client, owner, did, "t7k-m@example.com")
    owner_user = await _user(db_session, "t7k@example.com")
    m = await _user(db_session, "t7k-m@example.com")
    diary = await _diary(db_session, did)
    original = jpeg_with_exif()
    kept = await upload_photo(client, owner, original)
    await _entry_with_photo(client, owner, did, kept)
    kept_row = await _photo(db_session, kept)
    assert kept_row is not None and kept_row.thumbnail_s3_key is not None
    s3_client.delete_object(Bucket=photos_bucket, Key=kept_row.thumbnail_s3_key)
    lost = await make_photo(db_session, user=owner_user)  # row only: no object at all
    await make_entry_photo(db_session, entry=await make_entry(db_session, diary), photo=lost)
    await db_session.commit()
    lost_id = lost.id
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7k@example.com")

    from app.workers.handover import move_departing_photos, transfer_chosen_diaries

    assert await transfer_chosen_diaries(owner_id) == [uuid.UUID(did)]
    assert await move_departing_photos(owner_id) == 1  # the lost one is not counted
    await hard_delete_user(owner_id)

    assert (await db_session.execute(select(User.id).where(User.id == owner_id))).first() is None
    assert await _photo(db_session, lost_id) is None
    moved = await _photo(db_session, kept)
    assert moved is not None and moved.user_id == m.id
    assert moved.thumbnail_s3_key is None
    r = await client.get(f"/v1/photos/{kept}", params={"kind": "original"}, headers=m_headers)
    assert r.status_code == 200, r.text
    assert r.content == original


@pytest.mark.asyncio
async def test_photo_goes_to_owner_of_earliest_attachment(client, db_session):
    """Attached in two other people's diaries: the diary whose entry came first gets it."""
    from app.workers.hard_delete import hard_delete_user

    first, first_did = await _owner_with_diary(client, "t7l-a@example.com", tier="family")
    second, second_did = await _owner_with_diary(client, "t7l-b@example.com", tier="family")
    editor = await _add_member(client, first, first_did, "t7l-e@example.com", role="editor")
    created = await invite(client, second, second_did, "t7l-e@example.com", role="editor")
    assert (await accept(client, editor, created["token"])).status_code == 200
    pid = await upload_photo(client, editor, jpeg_with_exif())
    await _entry_with_photo(client, editor, first_did, pid, publish=False)  # earlier entry
    await _entry_with_photo(client, editor, second_did, pid, publish=False)
    first_user = await _user(db_session, "t7l-a@example.com")

    await _delete_account(client, editor)
    editor_id = await _make_due(db_session, "t7l-e@example.com")
    await hard_delete_user(editor_id)

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == first_user.id


# ---------------------------------------------------------------------------
# Review round 1: overlapping runs, sweeps and creates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_clash_counts_as_moved(client, db_session):
    from app.workers.handover import move_departing_photos, transfer_chosen_diaries
    from tests.fixtures.factories import make_entry, make_entry_photo, make_photo

    owner, did = await _owner_with_diary(client, "t7m@example.com")
    await _add_member(client, owner, did, "t7m-m@example.com")
    owner_user = await _user(db_session, "t7m@example.com")
    m = await _user(db_session, "t7m-m@example.com")
    mine = await make_photo(db_session, user=m, source="google_photos")
    theirs = await make_photo(db_session, user=owner_user, source="google_photos")
    mine.external_id = theirs.external_id = "g-count"
    await db_session.commit()
    entry = await make_entry(db_session, await _diary(db_session, did))
    await make_entry_photo(db_session, entry=entry, photo=theirs)
    await db_session.commit()
    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7m@example.com")

    await transfer_chosen_diaries(owner_id)
    assert await move_departing_photos(owner_id) == 1
    assert await move_departing_photos(owner_id) == 0  # nothing left to move


@pytest.mark.asyncio
async def test_second_pass_skips_while_lock_held(client, db_session, db_engine):
    from sqlalchemy import text

    from app.workers.beat_tasks import _process_hard_deletes
    from tests.fixtures.factories import make_user, mark_due_for_hard_delete

    user = await make_user(db_session, email="t7n@example.com")
    await mark_due_for_hard_delete(db_session, user)
    user_id = user.id

    async with db_engine.connect() as other_pass:
        await other_pass.execute(text("SELECT pg_advisory_lock(hashtext('hard_delete_pass'), 0)"))
        await other_pass.commit()
        with (
            capture_logs() as logs,
            patch("app.workers.beat_tasks.log", structlog.get_logger()),
        ):
            await _process_hard_deletes()
        assert [e["event"] for e in logs] == ["process_hard_deletes_skipped_locked"]
        assert (await db_session.execute(select(User.id).where(User.id == user_id))).first()
        await other_pass.execute(text("SELECT pg_advisory_unlock(hashtext('hard_delete_pass'), 0)"))
        await other_pass.commit()

    await _process_hard_deletes()  # the lock is free again: this pass does the work
    assert (await db_session.execute(select(User.id).where(User.id == user_id))).first() is None


@pytest.mark.asyncio
async def test_pass_releases_lock_after_failure(client, db_session, db_engine):
    from unittest.mock import AsyncMock

    from sqlalchemy import text

    from app.workers.beat_tasks import _process_hard_deletes

    with patch("app.workers.beat_tasks._run_hard_deletes", new=AsyncMock(side_effect=OSError)):
        with pytest.raises(OSError):
            await _process_hard_deletes()
    async with db_engine.connect() as conn:
        got = (
            await conn.execute(text("SELECT pg_try_advisory_lock(hashtext('hard_delete_pass'), 0)"))
        ).scalar_one()
        assert got is True
        await conn.execute(text("SELECT pg_advisory_unlock(hashtext('hard_delete_pass'), 0)"))
        await conn.commit()


@pytest.mark.asyncio
async def test_failure_log_has_no_row_values(client, db_session):
    from unittest.mock import AsyncMock

    from app.workers.beat_tasks import _process_hard_deletes
    from tests.fixtures.factories import make_user, mark_due_for_hard_delete

    user = await make_user(db_session, email="t7o@example.com")
    await mark_due_for_hard_delete(db_session, user)
    boom = RuntimeError("Key (owner_user_id, slug)=(x, emmas-diary) already exists")
    with (
        patch("app.workers.hard_delete.hard_delete_user", new=AsyncMock(side_effect=boom)),
        capture_logs() as logs,
        patch("app.workers.beat_tasks.log", structlog.get_logger()),
    ):
        await _process_hard_deletes()
    failed = [e for e in logs if e["event"] == "hard_delete_user_failed"]
    assert len(failed) == 1
    assert set(failed[0]) <= {"event", "log_level", "user_id", "error", "where"}
    assert failed[0]["error"] == "RuntimeError"
    assert "emmas" not in str(failed[0])


async def _editor_photo_in_owners_diary(client, db_session, prefix: str, body: bytes):
    owner, did = await _owner_with_diary(client, f"{prefix}@example.com", tier="family")
    editor = await _add_member(client, owner, did, f"{prefix}-e@example.com", role="editor")
    pid = await upload_photo(
        client, editor, body, content_type="image/heic" if body[4:8] == b"ftyp" else "image/jpeg"
    )
    await _entry_with_photo(client, editor, did, pid, publish=False)
    return owner, editor, did, pid


@pytest.mark.asyncio
async def test_mutual_photos_both_past_deadline_both_complete(
    client, db_session, s3_client, photos_bucket
):
    """Co-parents, each an editor in the other's diary with a photo there, both due."""
    from app.workers.beat_tasks import _process_hard_deletes

    a, a_did = await _owner_with_diary(client, "t7p-a@example.com", tier="family")
    b, b_did = await _owner_with_diary(client, "t7p-b@example.com", tier="family")
    created = await invite(client, a, a_did, "t7p-b@example.com", role="editor")
    assert (await accept(client, b, created["token"])).status_code == 200
    created = await invite(client, b, b_did, "t7p-a@example.com", role="editor")
    assert (await accept(client, a, created["token"])).status_code == 200
    a_photo = await upload_photo(client, a, jpeg_with_exif())
    await _entry_with_photo(client, a, b_did, a_photo, publish=False)  # in B's diary
    b_photo = await upload_photo(client, b, jpeg_with_exif())
    await _entry_with_photo(client, b, a_did, b_photo, publish=False)  # in A's diary
    await _delete_account(client, a)
    await _delete_account(client, b)
    a_id = await _make_due(db_session, "t7p-a@example.com")
    b_id = await _make_due(db_session, "t7p-b@example.com")

    await _process_hard_deletes()

    for user_id in (a_id, b_id):
        assert (await db_session.execute(select(User.id).where(User.id == user_id))).first() is None
        assert _keys(s3_client, photos_bucket, f"{user_id}/") == set()
    assert await _photo(db_session, a_photo) is None
    assert await _photo(db_session, b_photo) is None


@pytest.mark.asyncio
async def test_photo_follows_past_deadline_owners_handover(client, db_session):
    """A's photo is in B's diary; B hands that diary to C; A and B are both due."""
    from app.workers.beat_tasks import _process_hard_deletes

    b, b_did = await _owner_with_diary(client, "t7w-b@example.com", tier="family")
    a = await _add_member(client, b, b_did, "t7w-a@example.com", role="editor")
    c = await _add_member(client, b, b_did, "t7w-c@example.com")
    original = jpeg_with_exif()
    pid = await upload_photo(client, a, original)
    await _entry_with_photo(client, a, b_did, pid, publish=False)
    c_user = await _user(db_session, "t7w-c@example.com")
    await _delete_account(client, a)
    await _delete_account(client, b, {b_did: c_user.id})
    a_id = await _make_due(db_session, "t7w-a@example.com")
    b_id = await _make_due(db_session, "t7w-b@example.com")

    await _process_hard_deletes()

    for user_id in (a_id, b_id):
        assert (await db_session.execute(select(User.id).where(User.id == user_id))).first() is None
    diary = await _diary(db_session, b_did)
    assert diary is not None and diary.owner_user_id == c_user.id
    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == c_user.id
    r = await client.get(f"/v1/photos/{pid}", params={"kind": "original"}, headers=c)
    assert r.status_code == 200, r.text
    assert r.content == original


@pytest.mark.asyncio
async def test_move_to_target_in_grace_window_is_allowed(client, db_session):
    from app.workers.hard_delete import hard_delete_user

    owner, editor, did, pid = await _editor_photo_in_owners_diary(
        client, db_session, "t7q", jpeg_with_exif()
    )
    owner_user = await _user(db_session, "t7q@example.com")
    await _delete_account(client, editor)
    editor_id = await _make_due(db_session, "t7q-e@example.com")
    await _delete_account(client, owner)  # pending: may still restore

    await hard_delete_user(editor_id)

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_user.id


@pytest.mark.asyncio
async def test_move_to_admin_soft_deleted_target_is_allowed(client, db_session):
    """No deadline: the hourly job never deletes it, so refusing would stall for good."""
    from datetime import UTC, datetime

    from app.workers.hard_delete import hard_delete_user

    owner, editor, did, pid = await _editor_photo_in_owners_diary(
        client, db_session, "t7v", jpeg_with_exif()
    )
    owner_user = await _user(db_session, "t7v@example.com")
    await _delete_account(client, editor)
    editor_id = await _make_due(db_session, "t7v-e@example.com")
    await db_session.execute(
        update(User)
        .where(User.id == owner_user.id)
        .values(deleted_at=datetime.now(tz=UTC), hard_delete_after=None)
    )
    await db_session.commit()

    await hard_delete_user(editor_id)

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_user.id


@pytest.mark.asyncio
async def test_move_rederives_target_under_the_lock(client, db_session):
    from app.workers.handover import _move_photo

    owner, editor, did, pid = await _editor_photo_in_owners_diary(
        client, db_session, "t7r", jpeg_with_exif()
    )
    owner_user = await _user(db_session, "t7r@example.com")
    editor_user = await _user(db_session, "t7r-e@example.com")
    await register(client, "t7r-s@example.com")
    stranger_user = await _user(db_session, "t7r-s@example.com")

    # A stale choice (the stranger owns no diary holding the photo) is corrected.
    assert await _move_photo(uuid.UUID(pid), editor_user.id, stranger_user.id) is True
    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_user.id


@pytest.mark.asyncio
async def test_transfer_waits_for_recipients_creation_lock(client, db_session, db_engine):
    import asyncio

    from sqlalchemy import text

    from app.workers.handover import transfer_chosen_diaries

    owner, did = await _owner_with_diary(client, "t7s@example.com")
    await _add_member(client, owner, did, "t7s-m@example.com")
    m = await _user(db_session, "t7s-m@example.com")
    await _delete_account(client, owner)
    owner_user = await _user(db_session, "t7s@example.com")
    await db_session.execute(
        update(Diary).where(Diary.id == uuid.UUID(did)).values(handover_to_user_id=m.id)
    )
    await db_session.commit()

    async with db_engine.connect() as creating:  # the recipient's create_diary, mid-way
        await creating.execute(
            text("SELECT pg_advisory_lock(hashtext(:uid))").bindparams(uid=str(m.id))
        )
        await creating.commit()
        task = asyncio.create_task(transfer_chosen_diaries(owner_user.id))
        await asyncio.sleep(1.0)
        assert not task.done()
        await creating.execute(
            text("SELECT pg_advisory_unlock(hashtext(:uid))").bindparams(uid=str(m.id))
        )
        await creating.commit()
        assert await asyncio.wait_for(task, timeout=10) == [uuid.UUID(did)]


@pytest.mark.asyncio
async def test_soft_deleted_diary_is_not_handed_over(client, db_session):
    from datetime import UTC, datetime

    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7t@example.com")
    await _add_member(client, owner, did, "t7t-m@example.com")
    m = await _user(db_session, "t7t-m@example.com")
    await _delete_account(client, owner, {did: m.id})
    await db_session.execute(
        update(Diary).where(Diary.id == uuid.UUID(did)).values(deleted_at=datetime.now(tz=UTC))
    )
    await db_session.commit()
    owner_id = await _make_due(db_session, "t7t@example.com")

    with capture_logs() as logs, patch("app.workers.handover.log", structlog.get_logger()):
        await hard_delete_user(owner_id)

    skipped = [(e["diary_id"], e["reason"]) for e in logs if e["event"] == "handover_skipped"]
    assert skipped == [(did, "diary_deleted")]
    assert await _diary(db_session, did) is None


@pytest.mark.asyncio
async def test_display_sweep_does_not_point_a_moved_photo_at_the_old_prefix(
    client, db_session, s3_client, photos_bucket
):
    """The sweep read the photo, a hand-over moved it, then the sweep wrote its key."""
    import asyncio

    import app.services.photos as photos_service
    from app.workers.beat_tasks import create_missing_display_copies
    from app.workers.handover import _move_photo
    from tests.integration.photo_helpers import heic_bytes

    owner, editor, did, pid = await _editor_photo_in_owners_diary(
        client, db_session, "t7u", heic_bytes()
    )
    owner_user = await _user(db_session, "t7u@example.com")
    editor_user = await _user(db_session, "t7u-e@example.com")
    stale_key = f"{editor_user.id}/{pid}_display.enc"
    await db_session.execute(
        update(Photo).where(Photo.id == uuid.UUID(pid)).values(display_s3_key=None)
    )
    await db_session.commit()
    s3_client.delete_object(Bucket=photos_bucket, Key=stale_key)  # the one made at upload

    loop = asyncio.get_running_loop()
    real_get = photos_service.get_object_bytes

    def get_then_move(key):
        data = real_get(key)
        # The hand-over runs while the sweep is between its read and its write.
        asyncio.run_coroutine_threadsafe(
            _move_photo(uuid.UUID(pid), editor_user.id, owner_user.id), loop
        ).result(timeout=30)
        return data

    with patch.object(photos_service, "get_object_bytes", side_effect=get_then_move):
        assert await create_missing_display_copies() == 0

    photo = await _photo(db_session, pid)
    assert photo is not None and photo.user_id == owner_user.id
    assert photo.display_s3_key is None  # never a key under the editor's prefix
    assert stale_key not in _keys(s3_client, photos_bucket, f"{editor_user.id}/")  # cleaned up

    assert await create_missing_display_copies() == 1  # the next sweep, for the new owner
    photo = await _photo(db_session, pid)
    assert photo is not None and photo.display_s3_key == f"{owner_user.id}/{pid}_display.enc"
    r = await client.get(f"/v1/photos/{pid}", params={"kind": "full"}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_handover_keeps_wish_lists(client, db_session):
    from app.services.public_links import resolve_link
    from app.workers.hard_delete import hard_delete_user

    owner, did = await _owner_with_diary(client, "t7wl@example.com")
    await _add_member(client, owner, did, "t7wl-m@example.com")
    from app.core.database import get_session_factory

    async with get_session_factory()() as s:
        await s.execute(
            update(User).where(User.email == "t7wl@example.com").values(display_name="Mum")
        )
        await s.commit()
    wl = (
        await client.post(
            f"/v1/diaries/{did}/wish-lists",
            json={"title": "T", "list_date": "2026-12-14"},
            headers=owner,
        )
    ).json()
    r = await client.post(f"/v1/wish-lists/{wl['id']}/publish", headers=owner)
    assert r.status_code == 200, r.text
    token = r.json()["link"]["token"]
    m = await _user(db_session, "t7wl-m@example.com")

    await _delete_account(client, owner, {did: m.id})
    owner_id = await _make_due(db_session, "t7wl@example.com")
    await hard_delete_user(owner_id)

    db_session.expire_all()
    from app.models import WishList

    kept = (
        await db_session.execute(select(WishList).where(WishList.id == uuid.UUID(wl["id"])))
    ).scalar_one()
    assert str(kept.diary_id) == did
    _, diary = await resolve_link(db_session, token)
    assert diary.owner_user_id == m.id
