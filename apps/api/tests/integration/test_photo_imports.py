"""Picking photos from Google Photos onto an entry. Item 14.

Google is replaced by a fake client holding a scripted picker session. The
worker's task body runs directly (not through the broker); its re-queues are
captured and run in turn, as the broker would.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from app.core.security import encrypt_oauth_token
from app.models import DiaryPermission, EntryPhoto, OAuthToken, Photo, PhotoImport, User
from app.services.google_photos import (
    GoogleAuthError,
    GoogleUnavailable,
    PickedItem,
    PickerSession,
)
from tests.integration.photo_helpers import jpeg_with_exif, read_fixture, register_and_login

PICKER = "photospicker.mediaitems.readonly"
POLL_S = 2.0
CREATED = datetime(2026, 5, 16, 9, 30, tzinfo=UTC)


def _photo(i: int, data: bytes | Exception) -> tuple[PickedItem, bytes | Exception]:
    item = PickedItem(
        id=f"g-item-{i}",
        type="PHOTO",
        create_time=CREATED,
        base_url=f"https://lh3.googleusercontent.com/base-{i}",
        mime_type="image/jpeg",
        filename=f"IMG_{i}.jpg",
    )
    return item, data


def _video(i: int) -> tuple[PickedItem, None]:
    item = PickedItem(
        id=f"g-video-{i}",
        type="VIDEO",
        create_time=CREATED,
        base_url=f"https://lh3.googleusercontent.com/video-{i}",
        mime_type="video/mp4",
        filename=f"VID_{i}.mp4",
    )
    return item, None


class FakeGoogle:
    """A scripted Google Photos Picker: one session per create_session call."""

    def __init__(self, picks=(), *, polls_until_set: int = 1):
        self.items = [item for item, _ in picks]
        self.bytes = {item.id: data for item, data in picks}
        self.polls_until_set = polls_until_set
        self.get_error: Exception | None = None
        self.get_errors: list[Exception] = []  # raised once each, before get_error
        self.list_errors: list[Exception] = []  # raised once each
        self.sessions: list[str] = []
        self.get_calls = 0
        self.list_calls = 0
        self.downloads: list[str] = []
        self.deleted: list[str] = []
        self.tokens: list[str] = []

    def client(self, access_token: str, transport=None):
        self.tokens.append(access_token)
        return _FakeClient(self)


class _FakeClient:
    def __init__(self, google: FakeGoogle):
        self.g = google

    async def create_session(self, max_items: int = 50) -> PickerSession:
        assert max_items == 50
        sid = f"sess-{len(self.g.sessions) + 1}"
        self.g.sessions.append(sid)
        return PickerSession(
            id=sid,
            picker_uri=f"https://photos.google.com/picker/{sid}",
            items_set=False,
            poll_interval_s=POLL_S,
            timeout_s=1800.0,
            expire_time=datetime.now(tz=UTC) + timedelta(minutes=30),
        )

    async def get_session(self, session_id: str) -> PickerSession:
        self.g.get_calls += 1
        if self.g.get_errors:
            raise self.g.get_errors.pop(0)
        if self.g.get_error:
            raise self.g.get_error
        return PickerSession(
            id=session_id,
            picker_uri=f"https://photos.google.com/picker/{session_id}",
            items_set=self.g.get_calls >= self.g.polls_until_set,
            poll_interval_s=POLL_S,
            timeout_s=1800.0,
            expire_time=None,
        )

    async def list_items(self, session_id: str) -> list[PickedItem]:
        self.g.list_calls += 1
        if self.g.list_errors:
            raise self.g.list_errors.pop(0)
        return list(self.g.items)

    async def download(self, item: PickedItem) -> bytes:
        self.g.downloads.append(item.id)
        data = self.g.bytes[item.id]
        if isinstance(data, Exception):
            raise data
        return data

    async def delete_session(self, session_id: str) -> None:
        self.g.deleted.append(session_id)


def _google(fake: FakeGoogle):
    return patch("app.services.google_photos.GooglePhotosClient", side_effect=fake.client)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


async def _user(db_session, email: str) -> User:
    db_session.expire_all()
    return (await db_session.execute(select(User).where(User.email == email))).scalar_one()


async def _connect_photos(db_session, email: str, scopes=("calendar.readonly", PICKER)) -> None:
    user = await _user(db_session, email)
    db_session.add(
        OAuthToken(
            user_id=user.id,
            provider="google",
            access_token_ciphertext=encrypt_oauth_token("ya29.picker"),
            refresh_token_ciphertext=encrypt_oauth_token("1//refresh"),
            scopes_granted=list(scopes),
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()


async def _entry(
    client, headers, entry_date: str = "2026-05-16", diary: dict | None = None
) -> tuple[dict, dict]:
    if diary is None:
        r = await client.post("/v1/diaries", json={"name": "D", "timezone": "UTC"}, headers=headers)
        assert r.status_code == 201, r.text
        diary = r.json()
    r = await client.post(
        f"/v1/diaries/{diary['id']}/entries", json={"entry_date": entry_date}, headers=headers
    )
    assert r.status_code == 201, r.text
    return diary, r.json()


async def _owner_with_entry(client, db_session, prefix: str, *, connected: bool = True):
    email = f"{prefix}@example.com"
    auth = await register_and_login(client, email)
    _, entry = await _entry(client, auth)
    if connected:
        await _connect_photos(db_session, email)
    return auth, entry


async def _start(client, auth, entry_id: str, fake: FakeGoogle) -> dict:
    with _google(fake), patch("app.workers.tasks.process_photo_import.apply_async") as queued:
        r = await client.post(f"/v1/entries/{entry_id}/photo-imports", headers=auth)
    assert r.status_code == 201, r.text
    assert queued.call_count == 1
    return r.json()


async def _run_worker(import_id: str, fake: FakeGoogle, *, max_runs: int = 20):
    """Run the task body, then every re-queue it makes, until it stops.

    Returns (countdowns of each re-queue, location-refresh mock).
    """
    from app.workers.tasks import _process_photo_import

    queued: list[tuple[list, dict, float]] = []

    def _apply_async(args=None, kwargs=None, countdown=None, **_):
        queued.append((list(args or []), dict(kwargs or {}), countdown))

    pending: list[tuple[list, dict]] = [([import_id], {})]
    countdowns: list[float] = []
    with (
        _google(fake),
        patch("app.workers.tasks.process_photo_import.apply_async", side_effect=_apply_async),
        patch("app.workers.tasks.refresh_entry_location.delay") as location,
    ):
        runs = 0
        while pending:
            runs += 1
            assert runs <= max_runs, "the import never stopped re-queueing itself"
            args, kwargs = pending.pop(0)
            await _process_photo_import(*args, **kwargs)
            for a, k, c in queued:
                countdowns.append(c)
                pending.append((a, k))
            queued.clear()
    return countdowns, location


async def _import_row(db_session, import_id: str) -> PhotoImport:
    db_session.expire_all()
    return (
        await db_session.execute(select(PhotoImport).where(PhotoImport.id == uuid.UUID(import_id)))
    ).scalar_one()


async def _entry_photos(db_session, entry_id: str) -> list[Photo]:
    db_session.expire_all()
    return list(
        (
            await db_session.execute(
                select(Photo)
                .join(EntryPhoto, EntryPhoto.photo_id == Photo.id)
                .where(EntryPhoto.entry_id == uuid.UUID(entry_id))
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Starting an import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_import_returns_picker_uri_and_queues_task(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-create")
    fake = FakeGoogle()

    with _google(fake), patch("app.workers.tasks.process_photo_import.apply_async") as queued:
        r = await client.post(f"/v1/entries/{entry['id']}/photo-imports", headers=auth)

    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "picker_uri", "status"}
    assert body["picker_uri"] == "https://photos.google.com/picker/sess-1"
    assert body["status"] == "waiting"
    assert fake.tokens == ["ya29.picker"]
    assert "ya29" not in r.text

    queued.assert_called_once()
    assert queued.call_args.kwargs["args"] == [body["id"]]
    assert queued.call_args.kwargs["countdown"] == POLL_S

    row = await _import_row(db_session, body["id"])
    assert row.google_session_id == "sess-1"
    assert str(row.entry_id) == entry["id"]
    assert row.status == "waiting"
    assert row.expires_at is not None and row.expires_at > datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_viewer_cannot_start_import(client, db_session):
    owner, entry = await _owner_with_entry(client, db_session, "pi-viewer-owner")
    viewer = await register_and_login(client, "pi-viewer@example.com")
    await _connect_photos(db_session, "pi-viewer@example.com")
    diary_id = uuid.UUID(entry["diary_id"])
    db_session.add(
        DiaryPermission(
            diary_id=diary_id,
            user_id=(await _user(db_session, "pi-viewer@example.com")).id,
            role="viewer",
        )
    )
    await db_session.commit()
    r = await client.post(f"/v1/entries/{entry['id']}/publish", headers=owner)
    assert r.status_code == 200, r.text
    fake = FakeGoogle()

    with _google(fake), patch("app.workers.tasks.process_photo_import.apply_async") as queued:
        r = await client.post(f"/v1/entries/{entry['id']}/photo-imports", headers=viewer)

    assert r.status_code == 403, r.text
    assert fake.sessions == []
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_editor_can_start_import_into_own_library(client, db_session):
    owner, entry = await _owner_with_entry(client, db_session, "pi-ed-owner", connected=False)
    editor = await register_and_login(client, "pi-editor@example.com")
    await _connect_photos(db_session, "pi-editor@example.com")
    editor_id = (await _user(db_session, "pi-editor@example.com")).id
    db_session.add(
        DiaryPermission(diary_id=uuid.UUID(entry["diary_id"]), user_id=editor_id, role="editor")
    )
    await db_session.commit()
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])

    imp = await _start(client, editor, entry["id"], fake)
    await _run_worker(imp["id"], fake)

    photos = await _entry_photos(db_session, entry["id"])
    assert [p.user_id for p in photos] == [editor_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [None, ("calendar.readonly",)])
async def test_not_connected_returns_409(client, db_session, scopes):
    auth, entry = await _owner_with_entry(client, db_session, "pi-409", connected=False)
    if scopes is not None:
        await _connect_photos(db_session, "pi-409@example.com", scopes=scopes)
    fake = FakeGoogle()

    with _google(fake), patch("app.workers.tasks.process_photo_import.apply_async") as queued:
        r = await client.post(f"/v1/entries/{entry['id']}/photo-imports", headers=auth)

    assert r.status_code == 409, r.text
    assert r.json()["error"]["message"] == "photos_not_connected"
    assert fake.sessions == []
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_revoked_connection_returns_409(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-409-revoked")
    user = await _user(db_session, "pi-409-revoked@example.com")
    await db_session.execute(
        update(OAuthToken)
        .where(OAuthToken.user_id == user.id)
        .values(revoked_at=datetime.now(tz=UTC))
    )
    await db_session.commit()

    with _google(FakeGoogle()):
        r = await client.post(f"/v1/entries/{entry['id']}/photo-imports", headers=auth)

    assert r.status_code == 409
    assert r.json()["error"]["message"] == "photos_not_connected"


@pytest.mark.asyncio
async def test_queue_down_marks_failed_and_returns_503(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-queue-down")
    fake = FakeGoogle()

    with (
        _google(fake),
        patch(
            "app.workers.tasks.process_photo_import.apply_async",
            side_effect=ConnectionError("broker down"),
        ),
    ):
        r = await client.post(f"/v1/entries/{entry['id']}/photo-imports", headers=auth)

    assert r.status_code == 503, r.text
    assert r.json()["error"]["message"] == "queue_unavailable"
    rows = (await db_session.execute(select(PhotoImport))).scalars().all()
    assert [row.status for row in rows] == ["failed"]
    assert fake.deleted == ["sess-1"]


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finished_pick_copies_attaches_and_counts(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-copy")
    fake = FakeGoogle(
        [_photo(1, read_fixture("sample.jpg")), _video(2), _photo(3, jpeg_with_exif())]
    )

    imp = await _start(client, auth, entry["id"], fake)
    _, location = await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert row.status == "done"
    assert row.added_count == 2
    assert row.skipped_videos == 1
    assert row.failed_names == []

    photos = await _entry_photos(db_session, entry["id"])
    assert len(photos) == 2
    by_item = {p.external_id: p for p in photos}
    assert set(by_item) == {"g-item-1", "g-item-3"}
    assert all(p.source == "google_photos" and p.finalized_at for p in photos)
    assert by_item["g-item-1"].taken_at == CREATED  # no EXIF: Google's createTime
    assert by_item["g-item-3"].taken_at != CREATED  # EXIF wins
    assert fake.downloads == ["g-item-1", "g-item-3"]  # the video is never fetched

    assert fake.deleted == ["sess-1"]
    location.assert_called_once()
    assert location.call_args.args[0] == entry["id"]

    r = await client.get(f"/v1/photo-imports/{imp['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json() == {
        "id": imp["id"],
        "entry_id": entry["id"],
        "status": "done",
        "added_count": 2,
        "skipped_videos": 1,
        "failed_names": [],
    }


@pytest.mark.asyncio
async def test_repicked_item_reuses_library_photo(client, db_session):
    auth, first_entry = await _owner_with_entry(client, db_session, "pi-repick")
    _, second_entry = await _entry(
        client, auth, "2026-05-17", diary={"id": first_entry["diary_id"]}
    )
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])

    first = await _start(client, auth, first_entry["id"], fake)
    await _run_worker(first["id"], fake)
    second = await _start(client, auth, second_entry["id"], fake)
    await _run_worker(second["id"], fake)

    assert fake.downloads == ["g-item-1"]  # copied once
    (in_first,) = await _entry_photos(db_session, first_entry["id"])
    (in_second,) = await _entry_photos(db_session, second_entry["id"])
    assert in_first.id == in_second.id
    library = (
        (await db_session.execute(select(Photo).where(Photo.external_id == "g-item-1")))
        .scalars()
        .all()
    )
    assert len(library) == 1
    row = await _import_row(db_session, second["id"])
    assert (row.status, row.added_count) == ("done", 1)


@pytest.mark.asyncio
async def test_one_failed_download_is_named_and_others_succeed(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-fail-one")
    fake = FakeGoogle(
        [
            _photo(1, read_fixture("sample.jpg")),
            _photo(2, GoogleUnavailable("503")),
            _photo(3, jpeg_with_exif()),
        ]
    )

    imp = await _start(client, auth, entry["id"], fake)
    _, location = await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert row.status == "done"
    assert row.added_count == 2
    assert row.failed_names == ["IMG_2.jpg"]
    assert {p.external_id for p in await _entry_photos(db_session, entry["id"])} == {
        "g-item-1",
        "g-item-3",
    }
    location.assert_called_once()


@pytest.mark.asyncio
async def test_unsupported_file_is_named(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-unsupported")
    fake = FakeGoogle([_photo(1, b"%PDF-1.4 not a photo")])

    imp = await _start(client, auth, entry["id"], fake)
    _, location = await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.added_count, row.failed_names) == ("done", 0, ["IMG_1.jpg"])
    location.assert_not_called()


@pytest.mark.asyncio
async def test_task_finishes_with_no_web_polling(client, db_session):
    """Review Focus 3: the phone tab closes mid-pick; the server finishes alone."""
    auth, entry = await _owner_with_entry(client, db_session, "pi-no-web")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))], polls_until_set=3)
    imp = await _start(client, auth, entry["id"], fake)

    def _no_web(*args, **kwargs):
        raise AssertionError("the web page polled; it must not be needed")

    with patch.object(client, "get", side_effect=_no_web), patch.object(
        client, "post", side_effect=_no_web
    ):
        countdowns, _ = await _run_worker(imp["id"], fake)

    assert fake.get_calls == 3
    assert countdowns == [POLL_S, POLL_S]  # Google's poll interval between polls
    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.added_count) == ("done", 1)


@pytest.mark.asyncio
async def test_google_outage_while_polling_tries_again(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-poll-outage")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))], polls_until_set=2)
    fake.get_errors = [GoogleUnavailable("503")]
    imp = await _start(client, auth, entry["id"], fake)

    countdowns, _ = await _run_worker(imp["id"], fake)

    assert len(countdowns) == 1
    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.added_count) == ("done", 1)


@pytest.mark.asyncio
async def test_listing_outage_retries_then_fails(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-list-outage")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])
    fake.list_errors = [GoogleUnavailable("503")] * 10
    imp = await _start(client, auth, entry["id"], fake)

    countdowns, location = await _run_worker(imp["id"], fake)

    assert fake.list_calls == 4  # the first try and 3 retries
    assert countdowns == [POLL_S, POLL_S, POLL_S]
    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.added_count) == ("failed", 0)
    location.assert_not_called()


@pytest.mark.asyncio
async def test_listing_outage_recovers(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-list-recover")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])
    fake.list_errors = [GoogleUnavailable("503")] * 2
    imp = await _start(client, auth, entry["id"], fake)

    await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.added_count) == ("done", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["google_says_401", "disconnected"])
async def test_revoked_token_marks_needs_reconnect(client, db_session, how):
    auth, entry = await _owner_with_entry(client, db_session, f"pi-reconnect-{how}")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])
    imp = await _start(client, auth, entry["id"], fake)
    if how == "google_says_401":
        fake.get_error = GoogleAuthError("401")
    else:  # what "Disconnect Google" leaves behind: no token row
        user = await _user(db_session, f"pi-reconnect-{how}@example.com")
        await db_session.execute(sa_delete(OAuthToken).where(OAuthToken.user_id == user.id))
        await db_session.commit()

    countdowns, location = await _run_worker(imp["id"], fake)

    assert countdowns == []
    row = await _import_row(db_session, imp["id"])
    assert row.status == "needs_reconnect"
    assert fake.downloads == []
    location.assert_not_called()


@pytest.mark.asyncio
async def test_auth_error_mid_copy_keeps_what_was_added(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-reconnect-mid")
    fake = FakeGoogle(
        [_photo(1, read_fixture("sample.jpg")), _photo(2, GoogleAuthError("401"))]
    )
    imp = await _start(client, auth, entry["id"], fake)

    _, location = await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.added_count) == ("needs_reconnect", 1)
    location.assert_called_once()


@pytest.mark.asyncio
async def test_expired_session_marks_expired(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-expired")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))], polls_until_set=10_000)
    imp = await _start(client, auth, entry["id"], fake)
    await db_session.execute(
        update(PhotoImport)
        .where(PhotoImport.id == uuid.UUID(imp["id"]))
        .values(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    countdowns, _ = await _run_worker(imp["id"], fake)

    assert countdowns == []
    assert fake.get_calls == 0
    row = await _import_row(db_session, imp["id"])
    assert row.status == "expired"


@pytest.mark.asyncio
async def test_cancel_stops_polling(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-cancel")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))], polls_until_set=10_000)
    imp = await _start(client, auth, entry["id"], fake)

    r = await client.post(f"/v1/photo-imports/{imp['id']}/cancel", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    countdowns, _ = await _run_worker(imp["id"], fake)

    assert countdowns == []
    assert fake.get_calls == 0
    assert fake.deleted == ["sess-1"]
    row = await _import_row(db_session, imp["id"])
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_after_done_changes_nothing(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-cancel-done")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])
    imp = await _start(client, auth, entry["id"], fake)
    await _run_worker(imp["id"], fake)

    r = await client.post(f"/v1/photo-imports/{imp['id']}/cancel", headers=auth)

    assert r.status_code == 200
    assert r.json()["status"] == "done"


@pytest.mark.asyncio
async def test_dismissed_import_not_listed(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-dismiss")
    fake = FakeGoogle()
    first = await _start(client, auth, entry["id"], fake)
    middle = await _start(client, auth, entry["id"], fake)
    last = await _start(client, auth, entry["id"], fake)

    r = await client.post(f"/v1/photo-imports/{middle['id']}/dismiss", headers=auth)
    assert r.status_code == 204, r.text

    r = await client.get(f"/v1/entries/{entry['id']}/photo-imports", headers=auth)
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()] == [last["id"], first["id"]]  # newest first
    assert set(r.json()[0]) == {
        "id",
        "entry_id",
        "status",
        "added_count",
        "skipped_videos",
        "failed_names",
    }


@pytest.mark.asyncio
async def test_list_shows_only_the_callers_imports(client, db_session):
    owner, entry = await _owner_with_entry(client, db_session, "pi-list-owner")
    editor = await register_and_login(client, "pi-list-editor@example.com")
    await _connect_photos(db_session, "pi-list-editor@example.com")
    db_session.add(
        DiaryPermission(
            diary_id=uuid.UUID(entry["diary_id"]),
            user_id=(await _user(db_session, "pi-list-editor@example.com")).id,
            role="editor",
        )
    )
    await db_session.commit()
    fake = FakeGoogle()
    mine = await _start(client, owner, entry["id"], fake)
    await _start(client, editor, entry["id"], fake)

    r = await client.get(f"/v1/entries/{entry['id']}/photo-imports", headers=owner)

    assert [i["id"] for i in r.json()] == [mine["id"]]


@pytest.mark.asyncio
async def test_other_users_import_404(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-mine")
    imp = await _start(client, auth, entry["id"], FakeGoogle())
    stranger = await register_and_login(client, "pi-stranger@example.com")

    for method, path in [
        ("get", f"/v1/photo-imports/{imp['id']}"),
        ("post", f"/v1/photo-imports/{imp['id']}/cancel"),
        ("post", f"/v1/photo-imports/{imp['id']}/dismiss"),
    ]:
        r = await getattr(client, method)(path, headers=stranger)
        assert r.status_code == 404, (path, r.text)

    r = await client.get(f"/v1/entries/{entry['id']}/photo-imports", headers=stranger)
    assert r.status_code == 404

    row = await _import_row(db_session, imp["id"])
    assert (row.status, row.dismissed_at) == ("waiting", None)


@pytest.mark.asyncio
async def test_unexpected_crash_marks_failed(client, db_session):
    auth, entry = await _owner_with_entry(client, db_session, "pi-crash")
    fake = FakeGoogle([_photo(1, read_fixture("sample.jpg"))])
    fake.list_errors = [RuntimeError("bug")]
    imp = await _start(client, auth, entry["id"], fake)

    with pytest.raises(RuntimeError):
        await _run_worker(imp["id"], fake)

    row = await _import_row(db_session, imp["id"])
    assert row.status == "failed"


def test_imports_are_indexed_by_entry_and_user(sync_engine):
    from sqlalchemy import text

    with sync_engine.connect() as conn:
        indexdef = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_photo_imports_entry_user'")
        ).scalar_one()
    assert "(entry_id, user_id)" in indexdef
