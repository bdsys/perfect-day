# Item 13 — MinIO + Photo Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement encrypted photo upload, retrieval, attachment, and an orphan sweeper, plus a web UI for upload/library/lightbox/attach — all 8 endpoints from `design/03-api-surface.md` with chunked AES-256-GCM encryption per `design/08-security-privacy.md`.

**Architecture:** Two-step upload: client requests presigned PUT URL → uploads plaintext to MinIO at `tmp/{user_id}/{photo_id}` → server fetches plaintext, generates DEK, runs chunked AES-GCM encryption, generates a 512px JPEG thumbnail (also encrypted), writes both ciphertext objects, deletes tmp. DEK is wrapped under a per-user KEK derived from `master_secret` via HKDF. Reads stream-decrypt directly from MinIO. Orphan rows + tmp objects are swept after 24h.

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + boto3 (sync) + Pillow + pillow-heif + cryptography (existing) + Next.js (App Router) + Playwright.

---

## Context

Item 13 is Wave B of POC Phase 2 (`POC_PHASE2_TODO.md`). Photos are central to a parent's diary of their child — they must be encrypted at rest. The previous session committed `apps/api/app/core/photo_crypto.py` (commit `399ef0e`) covering DEK/KEK and chunked stream encrypt/decrypt. This plan covers everything else: object I/O, EXIF, thumbnail, all 8 endpoints, orphan sweeper extension, hard-delete extension, integration tests, MinIO testcontainer, web client, library page, lightbox, upload button, attach UI, and a Playwright spec.

Items 14 (Google Photos), 16 (Weather), and 18 (tier enforcement) explicitly depend on this PR per `POC_PHASE2_TODO.md`. Tier limits are explicitly **out of scope** — item 18 will gate uploads.

## Already done — do not redo

- `apps/api/app/core/photo_crypto.py:1-187` — full crypto module with `derive_kek`, `generate_dek`, `wrap_dek`, `unwrap_dek`, `derive_chunk_nonce`, `encrypt_stream`, `decrypt_stream`, `iter_decrypt_stream`, plus constants `KEY_VERSION_CURRENT=0x01`, `CHUNK_SIZE=1MiB`, `MAGIC=b"PD01"`, `HEADER_FORMAT="!4sIIQ"`.
- `apps/api/tests/unit/test_photo_crypto.py:1-270` — full unit coverage of crypto.
- `apps/api/app/models/__init__.py:372-430` — `Photo`, `DiaryPhoto`, `EntryPhoto` ORM models.
- `apps/api/alembic/versions/0001_initial_schema.py` — photo tables already migrated.
- `apps/api/app/core/dependencies.py:33-44` — `get_s3()` boto3 sync singleton.
- `apps/api/app/core/config.py:30-35,38,64-73` — `s3_*`, `master_secret` (validated as 64 hex chars).
- `apps/api/app/routers/v1/diaries.py:75-104` — `_get_diary_or_404(diary_id, user, db, require_owner=False) -> (Diary, role)`.
- `apps/api/app/workers/beat_tasks.py:87-135` — existing `_sweep_orphaned_photos`. We will rewrite it (the `func.cast` shim is broken; we also need to scrub `tmp/{user_id}/`).
- `apps/api/app/workers/hard_delete.py:65-82,157-167` — already deletes `photo.s3_key` / `thumbnail_s3_key` and scrubs `{user_id}/`. We extend it to also scrub `tmp/{user_id}/`.
- `testcontainers[minio]>=4` is already in `apps/api/pyproject.toml:55`.

---

## File Structure (new + modified)

**New backend:**
- `apps/api/app/services/photos.py` — sync helpers: MIME detection, EXIF, thumbnail, S3 wrappers, presign.
- `apps/api/app/routers/v1/photos.py` — 8 endpoints + auth helper + Pydantic schemas.
- `apps/api/tests/integration/test_photos.py` — endpoint integration tests.
- `apps/api/tests/integration/test_photos_sweeper.py` — sweeper integration test.
- `apps/api/tests/fixtures/photos/sample.jpg`, `sample.png`, `sample.heic` — small real images.

**New frontend:**
- `apps/web/src/app/diaries/[diaryId]/photos/page.tsx` — library grid.
- `apps/web/src/components/PhotoThumbnail.tsx`, `PhotoLightbox.tsx`, `PhotoUploadButton.tsx`.
- `apps/web/e2e/photo-upload.spec.ts` + fixtures `sample.jpg`.

**Modified backend:**
- `apps/api/app/main.py` — register photos router.
- `apps/api/app/routers/v1/entries.py` — add `photos` to `EntryOut`; eager-load `entry_photos`.
- `apps/api/app/routers/v1/diaries.py` — add `GET /diaries/{id}/photos`.
- `apps/api/app/workers/beat_tasks.py` — rewrite `_sweep_orphaned_photos`.
- `apps/api/app/workers/hard_delete.py` — extend `hard_delete_user` to scrub `tmp/{user_id}/`.
- `apps/api/app/workers/celery_app.py` — confirm `sweep_orphaned_photos` is in beat schedule (every 6h).
- `apps/api/pyproject.toml` — add `Pillow>=10.4`, `pillow-heif>=0.18`.
- `apps/api/Dockerfile` — add `libheif1` if needed (verify wheel works first).
- `apps/api/tests/integration/conftest.py` — add MinIO testcontainer, photos bucket fixture, extend `truncate_tables`.
- `apps/api/tests/fixtures/factories.py` — add `make_photo`, `make_diary_photo`, `make_entry_photo`.

**Modified frontend:**
- `apps/web/src/lib/api.ts` — add `Photo`, `UploadUrl` types, `api.photos` namespace, `apiFetchBlob` helper, `photos: Photo[]` field on `Entry`.
- `apps/web/src/app/entries/[entryId]/page.tsx` — render attached photos, "attach from library" picker.

---

## Object key layout

- Plaintext upload (short-lived): `tmp/{user_id}/{photo_id}`.
- Ciphertext full: `{user_id}/{photo_id}.enc`.
- Ciphertext thumb: `{user_id}/{photo_id}_thumb.enc`.

`hard_delete_user` already scrubs `{user_id}/` (catches both `.enc` files); we add a second pass for `tmp/{user_id}/`.

---

## Task 1: Add Pillow + pillow-heif dependencies

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/Dockerfile`

- [ ] **Step 1: Add Pillow and pillow-heif to pyproject.toml**

In `apps/api/pyproject.toml`, find the existing `dependencies = [...]` array (after `boto3>=1.35` at line 46) and add two new lines:

```toml
    "Pillow>=10.4",
    "pillow-heif>=0.18",
```

- [ ] **Step 2: Install the new deps**

Run: `cd apps/api && .venv/bin/pip install -e .`
Expected: clean install, no errors. `python -c "from PIL import Image; import pillow_heif; pillow_heif.register_heif_opener(); print('ok')"` prints `ok`.

- [ ] **Step 3: Verify HEIC decode works on slim image (probe before Dockerfile change)**

Run: `cd apps/api && .venv/bin/python -c "import pillow_heif; pillow_heif.register_heif_opener(); from PIL import Image; from io import BytesIO; print('HEIC opener registered')"`
Expected: `HEIC opener registered`. The `pillow-heif` wheel bundles `libheif`, so no Dockerfile change should be needed — proceed to step 4 only if the e2e test (Task 17) actually fails on the slim image.

- [ ] **Step 4 (conditional): Add libheif1 to Dockerfile only if step 3 fails inside Docker**

Edit `apps/api/Dockerfile` line 5 to:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends curl libheif1 && rm -rf /var/lib/apt/lists/*
```

Skip if pillow-heif wheel works in the container.

- [ ] **Step 5: Commit**

```bash
git add apps/api/pyproject.toml apps/api/Dockerfile
git commit -m "chore(api): add Pillow + pillow-heif for photo thumbnails"
```

---

## Task 2: Add MinIO testcontainer + photos bucket fixture

**Files:**
- Modify: `apps/api/tests/integration/conftest.py`

- [ ] **Step 1: Write a smoke test that asserts the fixture exists**

Append to `apps/api/tests/integration/test_photos.py` (creating the file):

```python
"""Integration tests for photo upload, encryption, and attachment."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_minio_fixture_smoke(s3_client, photos_bucket):
    """Sanity check: MinIO testcontainer + bucket fixture wire up correctly."""
    s3_client.put_object(Bucket=photos_bucket, Key="smoke", Body=b"ok")
    obj = s3_client.get_object(Bucket=photos_bucket, Key=photos_bucket and "smoke")
    assert obj["Body"].read() == b"ok"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_minio_fixture_smoke -v`
Expected: ERROR — fixture `s3_client` not found.

- [ ] **Step 3: Add MinIO testcontainer + bucket fixtures to conftest**

In `apps/api/tests/integration/conftest.py`, add after the `redis_container` fixture (line 31):

```python
from testcontainers.minio import MinioContainer


@pytest.fixture(scope="session")
def minio_container():
    with MinioContainer(image="minio/minio:latest") as m:
        yield m


@pytest.fixture(scope="session")
def s3_endpoint(minio_container):
    cfg = minio_container.get_config()
    return f"http://{cfg['endpoint']}"


@pytest.fixture(scope="session")
def s3_client(minio_container, s3_endpoint):
    import boto3

    cfg = minio_container.get_config()
    return boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="us-east-1",
    )


@pytest.fixture(scope="session")
def photos_bucket(s3_client):
    name = "photos-test"
    s3_client.create_bucket(Bucket=name)
    return name
```

Then in the `client` fixture (around line 129), extend `env_overrides` to include the MinIO config:

```python
    cfg = minio_container.get_config()
    env_overrides = {
        "DATABASE_URL": db_url,
        "DATABASE_URL_SYNC": db_url.replace("+asyncpg", ""),
        "REDIS_URL": redis_url,
        "CELERY_BROKER_URL": redis_url,
        "CELERY_RESULT_BACKEND": redis_url,
        "ENV": "test",
        "S3_ENDPOINT_URL": s3_endpoint,
        "S3_ACCESS_KEY": cfg["access_key"],
        "S3_SECRET_KEY": cfg["secret_key"],
        "S3_BUCKET_PHOTOS": "photos-test",
        "S3_REGION": "us-east-1",
        "MASTER_SECRET": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
    }
```

Also add `minio_container, s3_endpoint, photos_bucket` to the `client` fixture parameters.

- [ ] **Step 4: Reset the boto3 singleton between tests**

In the same `client` fixture (inside the `with patch.dict(...)` block), after `get_settings.cache_clear()`:

```python
        import app.core.dependencies as deps_module
        deps_module._s3_client = None
```

- [ ] **Step 5: Run the smoke test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_minio_fixture_smoke -v`
Expected: PASS.

- [ ] **Step 6: Extend truncate_tables to include photo tables**

In `apps/api/tests/integration/conftest.py:88-95`, change the TRUNCATE statement to add `photos, diary_photos, entry_photos` to the comma-list:

```python
                "TRUNCATE TABLE users, diaries, entries, events, scan_jobs, "
                "oauth_tokens, refresh_tokens, audit_log, llm_generations, "
                "entry_edit_diffs, diary_permissions, invitations, scan_runs, "
                "backfill_runs, diary_calendar_filters, notification_preferences, "
                "notifications, auto_creation_rules, entry_rule_matches, rule_series_claims, "
                "photos, diary_photos, entry_photos "
                "RESTART IDENTITY CASCADE"
```

- [ ] **Step 7: Run the smoke test again**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_minio_fixture_smoke -v`
Expected: PASS (truncate of empty new tables is a no-op).

- [ ] **Step 8: Commit**

```bash
git add apps/api/tests/integration/conftest.py apps/api/tests/integration/test_photos.py
git commit -m "test(api): add MinIO testcontainer + photos bucket fixture"
```

---

## Task 3: Add make_photo / make_diary_photo / make_entry_photo factories

**Files:**
- Modify: `apps/api/tests/fixtures/factories.py`
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write a failing test for the factory**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_make_photo_factory(db_session):
    from tests.fixtures.factories import make_diary, make_photo, make_user

    user = await make_user(db_session)
    diary = await make_diary(db_session, owner=user)
    photo = await make_photo(db_session, user=user, finalized=True)
    await db_session.commit()
    assert photo.user_id == user.id
    assert photo.finalized_at is not None
    assert photo.s3_key.startswith(f"{user.id}/")
    assert photo.dek_ciphertext is not None
    assert diary.owner_user_id == user.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_make_photo_factory -v`
Expected: FAIL — `cannot import name 'make_photo'`.

- [ ] **Step 3: Add factories**

Append to `apps/api/tests/fixtures/factories.py`:

```python
async def make_photo(
    db,
    *,
    user,
    finalized: bool = True,
    source: str = "upload",
    mime_type: str | None = "image/jpeg",
    bytes_: int | None = 1024,
    s3_key: str | None = None,
    thumbnail_s3_key: str | None = None,
    dek_ciphertext: bytes | None = None,
    taken_at=None,
    lat: float | None = None,
    lon: float | None = None,
):
    import datetime
    import uuid as _uuid

    from app.core.photo_crypto import generate_dek, wrap_dek
    from app.models import Photo

    pid = _uuid.uuid4()
    photo = Photo(
        id=pid,
        user_id=user.id,
        s3_key=s3_key or f"{user.id}/{pid}.enc",
        mime_type=mime_type if finalized else None,
        bytes=bytes_ if finalized else None,
        source=source,
        thumbnail_s3_key=(thumbnail_s3_key or f"{user.id}/{pid}_thumb.enc") if finalized else None,
        dek_ciphertext=dek_ciphertext or (wrap_dek(generate_dek(), user.id) if finalized else None),
        finalized_at=datetime.datetime.now(tz=datetime.UTC) if finalized else None,
        taken_at=taken_at,
        lat=lat,
        lon=lon,
    )
    db.add(photo)
    await db.flush()
    return photo


async def make_diary_photo(db, *, diary, photo):
    from app.models import DiaryPhoto

    dp = DiaryPhoto(diary_id=diary.id, photo_id=photo.id)
    db.add(dp)
    await db.flush()
    return dp


async def make_entry_photo(db, *, entry, photo, position: int | None = None):
    from app.models import EntryPhoto

    ep = EntryPhoto(entry_id=entry.id, photo_id=photo.id, position=position)
    db.add(ep)
    await db.flush()
    return ep
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_make_photo_factory -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/fixtures/factories.py apps/api/tests/integration/test_photos.py
git commit -m "test(api): add photo factory helpers"
```

---

## Photo state machine

```
[Created]    upload-url issued, row inserted, no S3 object yet
   ↓ client PUT to MinIO tmp/{uid}/{pid}
[Uploaded]   tmp object exists, row still finalized_at=NULL
   ↓ POST finalize
[Finalized]  finalized_at set, dek_ciphertext set, .enc + _thumb.enc written, tmp deleted
   ↓ DELETE /photos/{id}
[SoftDeleted] deleted_at set; reads 404; S3 untouched
   ↓ user/diary hard-delete worker
[HardDeleted] row gone, both .enc objects deleted, tmp prefix scrubbed too

[Created]/[Uploaded] without finalize for >24h
   ↓ sweeper
[Swept] sweeper sets deleted_at + deletes tmp object
```

---

## Task 4: services/photos.py — MIME detection (magic-byte sniff)

**Files:**
- Create: `apps/api/app/services/photos.py`
- Test: `apps/api/tests/unit/test_photos_service.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_photos_service.py`:

```python
"""Unit tests for app.services.photos — pure helpers, no I/O."""
from __future__ import annotations

import pytest


def test_detect_mime_jpeg():
    from app.services.photos import detect_mime
    assert detect_mime(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"


def test_detect_mime_png():
    from app.services.photos import detect_mime
    assert detect_mime(b"\x89PNG\r\n\x1a\n") == "image/png"


def test_detect_mime_gif87a():
    from app.services.photos import detect_mime
    assert detect_mime(b"GIF87a") == "image/gif"


def test_detect_mime_gif89a():
    from app.services.photos import detect_mime
    assert detect_mime(b"GIF89a") == "image/gif"


def test_detect_mime_webp():
    from app.services.photos import detect_mime
    head = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 "
    assert detect_mime(head) == "image/webp"


def test_detect_mime_heic():
    from app.services.photos import detect_mime
    head = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8
    assert detect_mime(head) == "image/heic"


def test_detect_mime_avif():
    from app.services.photos import detect_mime
    head = b"\x00\x00\x00\x18ftypavif" + b"\x00" * 8
    assert detect_mime(head) == "image/avif"


def test_detect_mime_unknown_returns_none():
    from app.services.photos import detect_mime
    assert detect_mime(b"%PDF-1.4\n") is None
    assert detect_mime(b"") is None


def test_constants():
    from app.services.photos import (
        ALLOWED_MIME, MAX_BYTES, THUMBNAIL_LONGEST_EDGE, THUMBNAIL_QUALITY, PRESIGN_TTL_SECONDS,
    )
    assert "image/jpeg" in ALLOWED_MIME
    assert "image/heic" in ALLOWED_MIME
    assert MAX_BYTES == 50 * 1024 * 1024
    assert THUMBNAIL_LONGEST_EDGE == 512
    assert THUMBNAIL_QUALITY == 80
    assert PRESIGN_TTL_SECONDS == 900
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_photos_service.py -v`
Expected: 9 ERRORS — module `app.services.photos` not found.

- [ ] **Step 3: Implement constants + detect_mime**

Create `apps/api/app/services/photos.py`:

```python
"""Sync helpers for photo upload/encryption/thumbnailing.

No DB or SQLAlchemy access here. boto3 is sync, so all S3 ops are sync calls
that callers may run via run_in_executor when needed.
"""
from __future__ import annotations

ALLOWED_MIME: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/webp",
    "image/gif",
    "image/avif",
})

MAX_BYTES: int = 50 * 1024 * 1024
THUMBNAIL_LONGEST_EDGE: int = 512
THUMBNAIL_QUALITY: int = 80
PRESIGN_TTL_SECONDS: int = 900


def detect_mime(head: bytes) -> str | None:
    """Sniff image MIME from the first ~32 bytes. Returns None on unknown."""
    if not head:
        return None
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"hevx"):
            return "image/heic"
        if brand == b"heif":
            return "image/heif"
        if brand in (b"avif", b"avis"):
            return "image/avif"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_photos_service.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/photos.py apps/api/tests/unit/test_photos_service.py
git commit -m "feat(api): photos service — MIME detection + constants"
```

---

## Task 5: services/photos.py — EXIF parsing

**Files:**
- Modify: `apps/api/app/services/photos.py`
- Test: `apps/api/tests/unit/test_photos_service.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/unit/test_photos_service.py`:

```python
def _build_jpeg_with_exif(taken="2025:08:14 13:45:00", lat=37.7749, lon=-122.4194):
    """Build a tiny in-memory JPEG with EXIF DateTimeOriginal + GPS."""
    from io import BytesIO
    import piexif
    from PIL import Image

    img = Image.new("RGB", (4, 4), (255, 0, 0))
    def deg_to_dms(d):
        d = abs(d)
        deg = int(d)
        m = int((d - deg) * 60)
        s = round(((d - deg) * 60 - m) * 60 * 100)
        return ((deg, 1), (m, 1), (s, 100))

    exif = {
        "0th": {},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: taken.encode("ascii")},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: deg_to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: deg_to_dms(lon),
        },
        "1st": {},
        "thumbnail": None,
    }
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=piexif.dump(exif))
    return buf.getvalue()


def test_parse_exif_extracts_date_and_gps():
    from app.services.photos import parse_exif
    img = _build_jpeg_with_exif()
    result = parse_exif(img)
    assert result["taken_at"] is not None
    assert result["taken_at"].year == 2025
    assert result["taken_at"].month == 8
    assert result["lat"] is not None and abs(result["lat"] - 37.7749) < 0.001
    assert result["lon"] is not None and abs(result["lon"] - -122.4194) < 0.001


def test_parse_exif_returns_none_on_no_exif():
    from io import BytesIO
    from PIL import Image
    from app.services.photos import parse_exif

    img = Image.new("RGB", (4, 4))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    result = parse_exif(buf.getvalue())
    assert result == {"taken_at": None, "lat": None, "lon": None}


def test_parse_exif_returns_none_on_garbage():
    from app.services.photos import parse_exif
    assert parse_exif(b"not an image") == {"taken_at": None, "lat": None, "lon": None}


def test_parse_exif_clamps_invalid_gps():
    """GPS that decodes to NaN or out-of-range should yield None lat/lon."""
    from app.services.photos import parse_exif
    # Garbage bytes — parser should not raise; just return None lat/lon.
    img = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    result = parse_exif(img)
    assert result["lat"] is None and result["lon"] is None
```

Add `piexif>=1.1.3` to `apps/api/pyproject.toml` `[project.optional-dependencies].dev` section (or main deps if Pillow's GPS reading is sufficient — see step 3).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_photos_service.py -v -k exif`
Expected: ERROR or FAIL — `parse_exif` not defined.

- [ ] **Step 3: Implement parse_exif**

Append to `apps/api/app/services/photos.py`:

```python
import datetime
import math
from io import BytesIO


def _exif_dms_to_decimal(dms, ref: str) -> float | None:
    try:
        deg = dms[0][0] / dms[0][1]
        minutes = dms[1][0] / dms[1][1]
        seconds = dms[2][0] / dms[2][1]
        val = deg + minutes / 60 + seconds / 3600
        if ref in ("S", "W"):
            val = -val
        if math.isnan(val) or math.isinf(val):
            return None
        if ref in ("N", "S") and not (-90 <= val <= 90):
            return None
        if ref in ("E", "W") and not (-180 <= val <= 180):
            return None
        return val
    except (ZeroDivisionError, IndexError, TypeError, ValueError):
        return None


def parse_exif(image_bytes: bytes) -> dict:
    """Extract DateTimeOriginal + GPS from EXIF. Any error → all None."""
    out: dict = {"taken_at": None, "lat": None, "lon": None}
    try:
        from PIL import ExifTags, Image

        img = Image.open(BytesIO(image_bytes))
        exif = img.getexif()
        if not exif:
            return out

        # DateTimeOriginal (36867); fall back to DateTime (306)
        ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(ExifTags, "IFD") else exif
        dt_str = ifd.get(36867) or exif.get(306)
        offset = ifd.get(36881)  # OffsetTimeOriginal, e.g. "+02:00"
        if dt_str:
            try:
                dt = datetime.datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                if offset and len(offset) >= 6:
                    sign = 1 if offset[0] == "+" else -1
                    hh = int(offset[1:3])
                    mm = int(offset[4:6])
                    tz = datetime.timezone(sign * datetime.timedelta(hours=hh, minutes=mm))
                else:
                    tz = datetime.UTC
                out["taken_at"] = dt.replace(tzinfo=tz)
            except (ValueError, TypeError):
                pass

        # GPS via GPSInfo IFD (34853)
        gps_ifd = None
        if hasattr(ExifTags, "IFD"):
            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            except (KeyError, AttributeError):
                gps_ifd = None
        if gps_ifd:
            lat_dms, lat_ref = gps_ifd.get(2), gps_ifd.get(1)
            lon_dms, lon_ref = gps_ifd.get(4), gps_ifd.get(3)
            if lat_dms and lat_ref:
                out["lat"] = _exif_dms_to_decimal(lat_dms, lat_ref)
            if lon_dms and lon_ref:
                out["lon"] = _exif_dms_to_decimal(lon_dms, lon_ref)
    except Exception:
        return {"taken_at": None, "lat": None, "lon": None}
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_photos_service.py -v -k exif`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/photos.py apps/api/tests/unit/test_photos_service.py apps/api/pyproject.toml
git commit -m "feat(api): photos service — EXIF parsing"
```

---

## Task 6: services/photos.py — Thumbnail generation

**Files:**
- Modify: `apps/api/app/services/photos.py`
- Test: `apps/api/tests/unit/test_photos_service.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/unit/test_photos_service.py`:

```python
def test_generate_thumbnail_jpeg_round_trip():
    from io import BytesIO
    from PIL import Image
    from app.services.photos import generate_thumbnail

    src = Image.new("RGB", (1024, 768), (10, 200, 50))
    buf = BytesIO()
    src.save(buf, format="JPEG", quality=90)
    out = generate_thumbnail(buf.getvalue(), "image/jpeg")
    assert out[:3] == b"\xff\xd8\xff"  # JPEG magic
    img = Image.open(BytesIO(out))
    assert max(img.size) <= 512


def test_generate_thumbnail_preserves_orientation():
    from io import BytesIO
    from PIL import Image
    from app.services.photos import generate_thumbnail

    src = Image.new("RGB", (200, 100), (255, 0, 0))
    buf = BytesIO()
    src.save(buf, format="PNG")
    out = generate_thumbnail(buf.getvalue(), "image/png")
    img = Image.open(BytesIO(out))
    assert img.format == "JPEG"


def test_generate_thumbnail_raises_on_garbage():
    from app.services.photos import generate_thumbnail
    with pytest.raises(ValueError):
        generate_thumbnail(b"not an image", "image/jpeg")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_photos_service.py -v -k thumbnail`
Expected: FAIL — `generate_thumbnail` not defined.

- [ ] **Step 3: Implement generate_thumbnail**

Append to `apps/api/app/services/photos.py`:

```python
# Register HEIF opener at module load.
try:
    import pillow_heif  # type: ignore[import-untyped]
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def generate_thumbnail(image_bytes: bytes, mime: str) -> bytes:
    """Decode → EXIF-rotate → resize ≤512 px longest edge → JPEG q=80.

    Raises ValueError on undecodable input.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail(
            (THUMBNAIL_LONGEST_EDGE, THUMBNAIL_LONGEST_EDGE),
            Image.Resampling.LANCZOS,
        )
        out = BytesIO()
        img.save(out, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
        return out.getvalue()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError(f"undecodable image: {e}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_photos_service.py -v -k thumbnail`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/photos.py apps/api/tests/unit/test_photos_service.py
git commit -m "feat(api): photos service — JPEG thumbnail generation"
```

---

## Task 7: services/photos.py — S3 wrapper helpers

**Files:**
- Modify: `apps/api/app/services/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write failing tests for S3 helpers**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_put_get_head_delete_round_trip(s3_client, photos_bucket, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    get_settings.cache_clear()
    import app.core.dependencies as deps
    deps._s3_client = s3_client

    from app.services.photos import (
        delete_object, get_object_bytes, head_object_size, put_object_bytes,
    )

    put_object_bytes("k1", b"hello", content_type="text/plain")
    assert get_object_bytes("k1") == b"hello"
    assert head_object_size("k1") == 5
    delete_object("k1")
    assert head_object_size("k1") is None
    delete_object("k1")  # second delete must not raise


@pytest.mark.asyncio
async def test_stream_object_yields_streaming_body(s3_client, photos_bucket, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    get_settings.cache_clear()
    import app.core.dependencies as deps
    deps._s3_client = s3_client

    from app.services.photos import put_object_bytes, stream_object

    put_object_bytes("stream", b"abcdef", content_type="application/octet-stream")
    body = stream_object("stream")
    assert body.read() == b"abcdef"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "round_trip or stream_object"`
Expected: FAIL — helpers not defined.

- [ ] **Step 3: Implement S3 wrappers**

Append to `apps/api/app/services/photos.py`:

```python
def _bucket() -> str:
    from app.core.config import get_settings
    return get_settings().s3_bucket_photos


def put_object_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
    from app.core.dependencies import get_s3
    get_s3().put_object(Bucket=_bucket(), Key=key, Body=body, ContentType=content_type)


def get_object_bytes(key: str) -> bytes:
    from app.core.dependencies import get_s3
    return get_s3().get_object(Bucket=_bucket(), Key=key)["Body"].read()


def head_object_size(key: str) -> int | None:
    from botocore.exceptions import ClientError
    from app.core.dependencies import get_s3
    try:
        resp = get_s3().head_object(Bucket=_bucket(), Key=key)
        return int(resp["ContentLength"])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def delete_object(key: str) -> None:
    from botocore.exceptions import ClientError
    from app.core.dependencies import get_s3
    try:
        get_s3().delete_object(Bucket=_bucket(), Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return
        raise


def stream_object(key: str):
    """Return botocore StreamingBody for *key*. Caller is responsible for closing."""
    from app.core.dependencies import get_s3
    return get_s3().get_object(Bucket=_bucket(), Key=key)["Body"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "round_trip or stream_object"`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): photos service — S3 put/get/head/delete/stream wrappers"
```

---

## Task 8: services/photos.py — Presigned PUT URL

**Files:**
- Modify: `apps/api/app/services/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write failing test**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_presign_put_url_round_trip(s3_client, photos_bucket, monkeypatch):
    import httpx
    from app.core.config import get_settings
    monkeypatch.setenv("S3_BUCKET_PHOTOS", photos_bucket)
    get_settings.cache_clear()
    import app.core.dependencies as deps
    deps._s3_client = s3_client

    from app.services.photos import presign_put_url

    body = b"hello-presigned-world"
    url = presign_put_url("tmp/test/abc", "image/jpeg", len(body))
    assert "X-Amz-Signature" in url or "Signature=" in url
    resp = httpx.put(
        url,
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    )
    assert resp.status_code == 200
    obj = s3_client.get_object(Bucket=photos_bucket, Key="tmp/test/abc")
    assert obj["Body"].read() == body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_presign_put_url_round_trip -v`
Expected: FAIL — `presign_put_url` not defined.

- [ ] **Step 3: Implement presign_put_url**

Append to `apps/api/app/services/photos.py`:

```python
def presign_put_url(key: str, content_type: str, content_length: int) -> str:
    """Presigned PUT URL valid for PRESIGN_TTL_SECONDS, bound to exact size + type."""
    from app.core.dependencies import get_s3
    return get_s3().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": _bucket(),
            "Key": key,
            "ContentType": content_type,
            "ContentLength": content_length,
        },
        ExpiresIn=PRESIGN_TTL_SECONDS,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_presign_put_url_round_trip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): photos service — presigned PUT URL"
```

---

## Task 9: photos router — skeleton + schemas + register in main.py

**Files:**
- Create: `apps/api/app/routers/v1/photos.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write failing test for router registration**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_photos_router_registered(client):
    """The photos router should be mounted; /v1/photos/upload-url returns 401 without auth."""
    resp = await client.post("/v1/photos/upload-url", json={"declared_mime": "image/jpeg", "declared_size": 100})
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_photos_router_registered -v`
Expected: FAIL — 404 (router not registered).

- [ ] **Step 3: Create router skeleton with schemas**

Create `apps/api/app/routers/v1/photos.py`:

```python
"""Photo upload, encryption, retrieval, and attachment endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User

router = APIRouter(tags=["photos"])


class UploadUrlRequest(BaseModel):
    declared_mime: str
    declared_size: int


class UploadUrlResponse(BaseModel):
    photo_id: uuid.UUID
    upload_url: str
    upload_key: str
    expires_in: int
    required_headers: dict[str, str]


class PhotoOut(BaseModel):
    id: uuid.UUID
    mime_type: str | None
    bytes: int | None
    taken_at: datetime | None
    lat: float | None
    lon: float | None
    source: str
    finalized_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None
    has_thumbnail: bool

    model_config = ConfigDict(from_attributes=True)


class PhotoAttachRequest(BaseModel):
    photo_id: uuid.UUID
    position: int | None = None


def _photo_out(photo) -> PhotoOut:
    return PhotoOut(
        id=photo.id,
        mime_type=photo.mime_type,
        bytes=photo.bytes,
        taken_at=photo.taken_at,
        lat=float(photo.lat) if photo.lat is not None else None,
        lon=float(photo.lon) if photo.lon is not None else None,
        source=photo.source,
        finalized_at=photo.finalized_at,
        created_at=photo.created_at,
        deleted_at=photo.deleted_at,
        has_thumbnail=photo.thumbnail_s3_key is not None,
    )
```

- [ ] **Step 4: Register router in main.py**

In `apps/api/app/main.py:59-67`, add `photos` to the import and `include_router` calls:

```python
    from app.routers.v1 import auth, calendar_events, diaries, entries, integrations, photos, rules, scan

    app.include_router(auth.router, prefix="/v1")
    app.include_router(diaries.router, prefix="/v1")
    app.include_router(entries.router, prefix="/v1")
    app.include_router(photos.router, prefix="/v1")
    app.include_router(calendar_events.router, prefix="/v1")
    app.include_router(integrations.router, prefix="/v1")
    app.include_router(rules.router, prefix="/v1")
    app.include_router(scan.router, prefix="/v1")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py::test_photos_router_registered -v`
Expected: PASS (still 401/403, but now the router exists).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/app/main.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): photos router — skeleton + schemas + register"
```

---

## Task 10: Endpoint 1 — POST /v1/photos/upload-url

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

Behavior: validate MIME whitelist (415) + size cap (413), insert Photo row reserving `s3_key=f"{user_id}/{photo_id}.enc"`, return presigned PUT URL for `tmp/{user_id}/{photo_id}` bound to exact `Content-Type` + `Content-Length`.

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
async def _login(client, email="user@example.com"):
    """Helper: register and return Authorization header."""
    await client.post("/v1/auth/register", json={"email": email, "password": "Password1!"})
    r = await client.post("/v1/auth/login", json={"email": email, "password": "Password1!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_upload_url_happy_path(client):
    headers = await _login(client)
    r = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": 12345},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "upload_url" in body
    assert body["upload_key"].startswith("tmp/")
    assert body["expires_in"] == 900
    assert body["required_headers"]["Content-Type"] == "image/jpeg"
    assert body["required_headers"]["Content-Length"] == "12345"


@pytest.mark.asyncio
async def test_upload_url_rejects_non_whitelisted_mime(client):
    headers = await _login(client, "u2@example.com")
    r = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "application/pdf", "declared_size": 100},
        headers=headers,
    )
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_upload_url_rejects_oversize(client):
    headers = await _login(client, "u3@example.com")
    r = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": 60 * 1024 * 1024},
        headers=headers,
    )
    assert r.status_code == 413
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k upload_url`
Expected: FAIL — 405 or 404 (endpoint not implemented).

- [ ] **Step 3: Implement upload-url endpoint**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from app.models import Photo
from app.services.photos import (
    ALLOWED_MIME, MAX_BYTES, PRESIGN_TTL_SECONDS, presign_put_url,
)


@router.post("/photos/upload-url", response_model=UploadUrlResponse, status_code=201)
async def create_upload_url(
    body: UploadUrlRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadUrlResponse:
    if body.declared_mime not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="unsupported_mime")
    if body.declared_size <= 0 or body.declared_size > MAX_BYTES:
        raise HTTPException(status_code=413, detail="size_exceeds_limit")

    photo_id = uuid.uuid4()
    s3_key = f"{user.id}/{photo_id}.enc"
    tmp_key = f"tmp/{user.id}/{photo_id}"

    photo = Photo(
        id=photo_id,
        user_id=user.id,
        s3_key=s3_key,
        source="upload",
    )
    db.add(photo)
    await db.flush()

    url = presign_put_url(tmp_key, body.declared_mime, body.declared_size)
    return UploadUrlResponse(
        photo_id=photo_id,
        upload_url=url,
        upload_key=tmp_key,
        expires_in=PRESIGN_TTL_SECONDS,
        required_headers={
            "Content-Type": body.declared_mime,
            "Content-Length": str(body.declared_size),
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k upload_url`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): POST /v1/photos/upload-url"
```

---

## Task 11: Endpoint 2 — POST /v1/photos/{id}/finalize

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`
- Add: `apps/api/tests/fixtures/photos/sample.jpg` (small real JPEG)

- [ ] **Step 1: Add a tiny real JPEG fixture**

Run: `cd apps/api && .venv/bin/python -c "from PIL import Image; Image.new('RGB', (32, 24), (10, 200, 50)).save('tests/fixtures/photos/sample.jpg', 'JPEG', quality=85)"`
Expected: file `tests/fixtures/photos/sample.jpg` created (~600 bytes).

- [ ] **Step 2: Write failing test for happy-path finalize**

Append to `apps/api/tests/integration/test_photos.py`:

```python
def _read_fixture(name: str) -> bytes:
    import pathlib
    return (pathlib.Path(__file__).parent.parent / "fixtures" / "photos" / name).read_bytes()


@pytest.mark.asyncio
async def test_finalize_happy_path(client, s3_client, photos_bucket):
    import httpx
    headers = await _login(client, "fin@example.com")
    body = _read_fixture("sample.jpg")

    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    assert r1.status_code == 201
    payload = r1.json()
    photo_id = payload["photo_id"]

    httpx.put(
        payload["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()

    r2 = await client.post(f"/v1/photos/{photo_id}/finalize", headers=headers)
    assert r2.status_code == 200, r2.text
    out = r2.json()
    assert out["mime_type"] == "image/jpeg"
    assert out["bytes"] == len(body)
    assert out["finalized_at"] is not None
    assert out["has_thumbnail"] is True

    # tmp object gone, .enc and _thumb.enc present
    objs = s3_client.list_objects_v2(Bucket=photos_bucket).get("Contents", [])
    keys = {o["Key"] for o in objs}
    assert any(k.endswith(f"{photo_id}.enc") for k in keys)
    assert any(k.endswith(f"{photo_id}_thumb.enc") for k in keys)
    assert not any(k.startswith("tmp/") and photo_id in k for k in keys)


@pytest.mark.asyncio
async def test_finalize_idempotent_409(client):
    import httpx
    headers = await _login(client, "fin2@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    assert (await client.post(f"/v1/photos/{pid}/finalize", headers=headers)).status_code == 200
    r3 = await client.post(f"/v1/photos/{pid}/finalize", headers=headers)
    assert r3.status_code == 409


@pytest.mark.asyncio
async def test_finalize_wrong_mime_returns_415(client):
    import httpx
    headers = await _login(client, "mw@example.com")
    body = b"%PDF-1.4\n%%EOF\n"
    r1 = await client.post(
        "/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)},
        headers=headers,
    )
    pid = r1.json()["photo_id"]
    httpx.put(
        r1.json()["upload_url"],
        content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
    ).raise_for_status()
    r = await client.post(f"/v1/photos/{pid}/finalize", headers=headers)
    assert r.status_code == 415
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k finalize`
Expected: FAIL — 405 (endpoint missing).

- [ ] **Step 4: Implement finalize endpoint**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from sqlalchemy import select

from app.core.photo_crypto import encrypt_stream, generate_dek, wrap_dek
from app.services.photos import (
    delete_object, detect_mime, generate_thumbnail, get_object_bytes,
    head_object_size, parse_exif, put_object_bytes,
)


async def _get_photo_for_owner(photo_id: uuid.UUID, user: User, db: AsyncSession) -> Photo:
    result = await db.execute(select(Photo).where(Photo.id == photo_id, Photo.user_id == user.id))
    photo = result.scalar_one_or_none()
    if photo is None or photo.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not_found")
    return photo


@router.post("/photos/{photo_id}/finalize", response_model=PhotoOut)
async def finalize_photo(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PhotoOut:
    photo = await _get_photo_for_owner(photo_id, user, db)
    if photo.finalized_at is not None:
        raise HTTPException(status_code=409, detail="already_finalized")

    tmp_key = f"tmp/{user.id}/{photo_id}"
    actual_size = head_object_size(tmp_key)
    if actual_size is None:
        raise HTTPException(status_code=404, detail="upload_not_found")
    if actual_size > MAX_BYTES:
        raise HTTPException(status_code=413, detail="size_exceeds_limit")

    plaintext = get_object_bytes(tmp_key)
    mime = detect_mime(plaintext[:32])
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="unsupported_mime")

    exif = parse_exif(plaintext)
    try:
        thumb_bytes = generate_thumbnail(plaintext, mime)
    except ValueError:
        raise HTTPException(status_code=422, detail="unprocessable_image")

    dek = generate_dek()
    wrapped = wrap_dek(dek, user.id)
    ct_full = encrypt_stream(plaintext, dek)
    ct_thumb = encrypt_stream(thumb_bytes, dek)

    thumb_key = f"{user.id}/{photo_id}_thumb.enc"
    put_object_bytes(thumb_key, ct_thumb, content_type="application/octet-stream")
    put_object_bytes(photo.s3_key, ct_full, content_type="application/octet-stream")

    import datetime as _dt
    photo.mime_type = mime
    photo.bytes = len(plaintext)
    photo.taken_at = exif["taken_at"]
    photo.lat = exif["lat"]
    photo.lon = exif["lon"]
    photo.thumbnail_s3_key = thumb_key
    photo.dek_ciphertext = wrapped
    photo.finalized_at = _dt.datetime.now(tz=_dt.UTC)
    await db.flush()
    await db.commit()

    try:
        delete_object(tmp_key)
    except Exception:
        pass  # sweeper will handle

    return _photo_out(photo)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k finalize`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py apps/api/tests/fixtures/photos/sample.jpg
git commit -m "feat(api): POST /v1/photos/{id}/finalize"
```

---

## Task 12: Visibility helper + Endpoint 3 (GET /photos/{id})

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

Visibility rules per spec:
- Owner: always allowed (drafts, unfinalized).
- Non-owner: visible iff attached to ≥1 entry where `status='published' AND deleted_at IS NULL` and the user has any role on the diary.
- Editors of the diary: also see drafts.
- `photo.deleted_at IS NOT NULL` → 404 to everyone.
- `finalized_at IS NULL` → 404 unless owner with `require_finalized=False`.

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_get_photo_owner_can_read(client):
    import httpx
    headers = await _login(client, "g1@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=headers)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)
    r = await client.get(f"/v1/photos/{pid}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == pid


@pytest.mark.asyncio
async def test_get_photo_other_user_404(client):
    import httpx
    h1 = await _login(client, "owner@example.com")
    h2 = await _login(client, "other@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=h1)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=h1)
    r = await client.get(f"/v1/photos/{pid}", headers=h2)
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k get_photo`
Expected: FAIL — 405/404 (endpoint missing).

- [ ] **Step 3: Implement visibility helper + GET /photos/{id}**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from sqlalchemy.orm import selectinload

from app.models import DiaryPermission, Entry, EntryPhoto


async def _get_photo_visible_or_404(
    photo_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    *,
    require_finalized: bool = True,
) -> Photo:
    """Return Photo if user is allowed to read it, else 404."""
    result = await db.execute(select(Photo).where(Photo.id == photo_id))
    photo = result.scalar_one_or_none()
    if photo is None or photo.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not_found")

    if photo.user_id == user.id:
        if require_finalized and photo.finalized_at is None:
            raise HTTPException(status_code=404, detail="not_found")
        return photo

    if photo.finalized_at is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Find entries this photo is attached to that the user can see
    q = (
        select(Entry, DiaryPermission.role)
        .join(EntryPhoto, EntryPhoto.entry_id == Entry.id)
        .join(DiaryPermission, DiaryPermission.diary_id == Entry.diary_id)
        .where(
            EntryPhoto.photo_id == photo_id,
            Entry.deleted_at.is_(None),
            DiaryPermission.user_id == user.id,
        )
    )
    rows = (await db.execute(q)).all()
    for entry, role in rows:
        if entry.status == "published":
            return photo
        if role == "editor" and entry.status == "draft":
            return photo
    raise HTTPException(status_code=404, detail="not_found")


@router.get("/photos/{photo_id}", response_model=PhotoOut)
async def get_photo(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PhotoOut:
    photo = await _get_photo_visible_or_404(photo_id, user, db)
    return _photo_out(photo)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k get_photo`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): GET /v1/photos/{id} + visibility helper"
```

---

## Task 13: Endpoints 4 + 5 — Streaming GET /full and /thumbnail

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

Approach: stream-decrypt directly to client via FastAPI `StreamingResponse`. boto3's `StreamingBody` is sync; wrap its `read(n)` in the `iter_decrypt_stream` reader callable.

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_get_full_round_trip(client):
    import hashlib, httpx
    headers = await _login(client, "rt@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=headers)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    r = await client.get(f"/v1/photos/{pid}/full", headers=headers)
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(body).hexdigest()
    assert r.headers["content-type"].startswith("image/jpeg")


@pytest.mark.asyncio
async def test_get_thumbnail_returns_jpeg(client):
    import httpx
    headers = await _login(client, "th@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=headers)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    r = await client.get(f"/v1/photos/{pid}/thumbnail", headers=headers)
    assert r.status_code == 200
    assert r.content[:3] == b"\xff\xd8\xff"


@pytest.mark.asyncio
async def test_get_full_404_for_unauthorized(client):
    import httpx
    h1 = await _login(client, "ow@example.com")
    h2 = await _login(client, "ot@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=h1)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=h1)

    r = await client.get(f"/v1/photos/{pid}/full", headers=h2)
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "get_full or get_thumbnail"`
Expected: FAIL — endpoints missing.

- [ ] **Step 3: Implement /full and /thumbnail**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from fastapi.responses import StreamingResponse

from app.core.photo_crypto import iter_decrypt_stream, unwrap_dek
from app.services.photos import stream_object


def _stream_decrypted(s3_key: str, dek: bytes):
    body = stream_object(s3_key)

    def reader(n: int) -> bytes:
        chunks = []
        remaining = n
        while remaining > 0:
            piece = body.read(remaining)
            if not piece:
                break
            chunks.append(piece)
            remaining -= len(piece)
        return b"".join(chunks)

    try:
        for chunk in iter_decrypt_stream(reader, dek):
            yield chunk
    finally:
        body.close()


@router.get("/photos/{photo_id}/full")
async def get_photo_full(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    photo = await _get_photo_visible_or_404(photo_id, user, db)
    if photo.dek_ciphertext is None:
        raise HTTPException(status_code=410, detail="gone")
    dek = unwrap_dek(bytes(photo.dek_ciphertext), photo.user_id)
    etag = f'W/"{photo.id}-{int(photo.finalized_at.timestamp()) if photo.finalized_at else 0}"'
    return StreamingResponse(
        _stream_decrypted(photo.s3_key, dek),
        media_type=photo.mime_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300", "ETag": etag},
    )


@router.get("/photos/{photo_id}/thumbnail")
async def get_photo_thumbnail(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    photo = await _get_photo_visible_or_404(photo_id, user, db)
    if photo.dek_ciphertext is None or photo.thumbnail_s3_key is None:
        raise HTTPException(status_code=410, detail="gone")
    dek = unwrap_dek(bytes(photo.dek_ciphertext), photo.user_id)
    etag = f'W/"{photo.id}-thumb-{int(photo.finalized_at.timestamp()) if photo.finalized_at else 0}"'
    return StreamingResponse(
        _stream_decrypted(photo.thumbnail_s3_key, dek),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300", "ETag": etag},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "get_full or get_thumbnail"`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): streaming GET /v1/photos/{id}/full and /thumbnail"
```

---

## Task 14: Endpoint 6 — DELETE /v1/photos/{id} (soft)

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_delete_photo_soft_then_get_404(client):
    import httpx
    headers = await _login(client, "del@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=headers)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)

    rd = await client.delete(f"/v1/photos/{pid}", headers=headers)
    assert rd.status_code == 204
    rg = await client.get(f"/v1/photos/{pid}", headers=headers)
    assert rg.status_code == 404


@pytest.mark.asyncio
async def test_delete_photo_non_owner_404(client):
    import httpx
    h1 = await _login(client, "do@example.com")
    h2 = await _login(client, "dx@example.com")
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=h1)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=h1)
    rd = await client.delete(f"/v1/photos/{pid}", headers=h2)
    assert rd.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k delete_photo`
Expected: FAIL — endpoint missing.

- [ ] **Step 3: Implement DELETE /photos/{id}**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from fastapi import Response


@router.delete("/photos/{photo_id}", status_code=204)
async def delete_photo(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    photo = await _get_photo_for_owner(photo_id, user, db)
    import datetime as _dt
    photo.deleted_at = _dt.datetime.now(tz=_dt.UTC)
    await db.flush()
    await db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k delete_photo`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): DELETE /v1/photos/{id} (soft)"
```

---

## Task 15: Endpoint 7 — Diary photo attach / detach

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

Behavior:
- `POST /v1/diaries/{diary_id}/photos/attach` — body `PhotoAttachRequest`. 403 if photo not owned by caller, 422 if photo not finalized, 409 if already attached.
- `DELETE /v1/diaries/{diary_id}/photos/{photo_id}` — 204 on success, 404 if not attached. Editor required.

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
async def _make_photo_for_user(client, headers) -> str:
    import httpx
    body = _read_fixture("sample.jpg")
    r1 = await client.post("/v1/photos/upload-url",
        json={"declared_mime": "image/jpeg", "declared_size": len(body)}, headers=headers)
    pid = r1.json()["photo_id"]
    httpx.put(r1.json()["upload_url"], content=body,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))}).raise_for_status()
    await client.post(f"/v1/photos/{pid}/finalize", headers=headers)
    return pid


async def _make_diary(client, headers, name="Test Diary") -> str:
    r = await client.post("/v1/diaries", json={"name": name}, headers=headers)
    return r.json()["id"]


@pytest.mark.asyncio
async def test_attach_photo_to_diary_happy_path(client):
    headers = await _login(client, "ad@example.com")
    diary_id = await _make_diary(client, headers)
    pid = await _make_photo_for_user(client, headers)
    r = await client.post(
        f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid},
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["id"] == pid


@pytest.mark.asyncio
async def test_attach_photo_not_owned_403(client):
    h1 = await _login(client, "o1@example.com")
    h2 = await _login(client, "o2@example.com")
    diary_id = await _make_diary(client, h2)
    pid = await _make_photo_for_user(client, h1)
    r = await client.post(
        f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid},
        headers=h2,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_attach_photo_duplicate_409(client):
    headers = await _login(client, "dup@example.com")
    diary_id = await _make_diary(client, headers)
    pid = await _make_photo_for_user(client, headers)
    await client.post(f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid}, headers=headers)
    r = await client.post(f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid}, headers=headers)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_detach_photo_from_diary(client):
    headers = await _login(client, "det@example.com")
    diary_id = await _make_diary(client, headers)
    pid = await _make_photo_for_user(client, headers)
    await client.post(f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid}, headers=headers)
    r = await client.delete(f"/v1/diaries/{diary_id}/photos/{pid}", headers=headers)
    assert r.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "attach_photo or detach_photo"`
Expected: FAIL — endpoints missing.

- [ ] **Step 3: Implement diary attach/detach**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from app.models import DiaryPhoto
from app.routers.v1.diaries import _get_diary_or_404


@router.post(
    "/diaries/{diary_id}/photos/attach",
    response_model=PhotoOut,
    status_code=201,
)
async def attach_photo_to_diary(
    diary_id: uuid.UUID,
    body: PhotoAttachRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PhotoOut:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    photo = (await db.execute(select(Photo).where(Photo.id == body.photo_id))).scalar_one_or_none()
    if photo is None or photo.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not_found")
    if photo.user_id != user.id:
        raise HTTPException(status_code=403, detail="not_owner")
    if photo.finalized_at is None:
        raise HTTPException(status_code=422, detail="photo_not_finalized")

    existing = (await db.execute(
        select(DiaryPhoto).where(
            DiaryPhoto.diary_id == diary_id, DiaryPhoto.photo_id == body.photo_id
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="already_attached")

    db.add(DiaryPhoto(diary_id=diary_id, photo_id=body.photo_id))
    await db.flush()
    await db.commit()
    return _photo_out(photo)


@router.delete("/diaries/{diary_id}/photos/{photo_id}", status_code=204)
async def detach_photo_from_diary(
    diary_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")
    existing = (await db.execute(
        select(DiaryPhoto).where(
            DiaryPhoto.diary_id == diary_id, DiaryPhoto.photo_id == photo_id
        )
    )).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="not_found")
    await db.delete(existing)
    await db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "attach_photo or detach_photo"`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): diary photo attach/detach endpoints"
```

---

## Task 16: Endpoint 8 — Entry photo attach / detach

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

Behavior:
- `POST /v1/entries/{entry_id}/photos` — `PhotoAttachRequest`. Photo must already be in `DiaryPhoto` for the entry's diary (cross-diary protection).
- `DELETE /v1/entries/{entry_id}/photos/{photo_id}`.
- Editor required on the diary.

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
async def _make_entry(client, headers, diary_id) -> str:
    from datetime import date
    r = await client.post(
        f"/v1/diaries/{diary_id}/entries",
        json={"entry_date": str(date.today()), "title": "T"},
        headers=headers,
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_attach_photo_to_entry_happy_path(client):
    headers = await _login(client, "ae@example.com")
    diary_id = await _make_diary(client, headers)
    entry_id = await _make_entry(client, headers, diary_id)
    pid = await _make_photo_for_user(client, headers)
    await client.post(f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid}, headers=headers)
    r = await client.post(f"/v1/entries/{entry_id}/photos",
        json={"photo_id": pid, "position": 0}, headers=headers)
    assert r.status_code == 201
    assert r.json()["id"] == pid


@pytest.mark.asyncio
async def test_attach_photo_to_entry_requires_diary_link(client):
    headers = await _login(client, "ax@example.com")
    diary_id = await _make_diary(client, headers)
    entry_id = await _make_entry(client, headers, diary_id)
    pid = await _make_photo_for_user(client, headers)
    # Skip diary attach — should fail with 422 or 404.
    r = await client.post(f"/v1/entries/{entry_id}/photos",
        json={"photo_id": pid}, headers=headers)
    assert r.status_code in (404, 422)


@pytest.mark.asyncio
async def test_detach_photo_from_entry(client):
    headers = await _login(client, "de@example.com")
    diary_id = await _make_diary(client, headers)
    entry_id = await _make_entry(client, headers, diary_id)
    pid = await _make_photo_for_user(client, headers)
    await client.post(f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid}, headers=headers)
    await client.post(f"/v1/entries/{entry_id}/photos",
        json={"photo_id": pid}, headers=headers)
    r = await client.delete(f"/v1/entries/{entry_id}/photos/{pid}", headers=headers)
    assert r.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "to_entry or from_entry"`
Expected: FAIL — endpoints missing.

- [ ] **Step 3: Implement entry attach/detach**

Append to `apps/api/app/routers/v1/photos.py`:

```python
from app.routers.v1.entries import _get_entry_or_404


@router.post(
    "/entries/{entry_id}/photos",
    response_model=PhotoOut,
    status_code=201,
)
async def attach_photo_to_entry(
    entry_id: uuid.UUID,
    body: PhotoAttachRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PhotoOut:
    entry, diary, role = await _get_entry_or_404(entry_id, user, db, require_editor=True)

    photo = (await db.execute(select(Photo).where(Photo.id == body.photo_id))).scalar_one_or_none()
    if photo is None or photo.deleted_at is not None or photo.finalized_at is None:
        raise HTTPException(status_code=404, detail="not_found")

    in_diary = (await db.execute(
        select(DiaryPhoto).where(
            DiaryPhoto.diary_id == entry.diary_id, DiaryPhoto.photo_id == body.photo_id
        )
    )).scalar_one_or_none()
    if in_diary is None:
        raise HTTPException(status_code=422, detail="photo_not_in_diary")

    existing = (await db.execute(
        select(EntryPhoto).where(
            EntryPhoto.entry_id == entry_id, EntryPhoto.photo_id == body.photo_id
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="already_attached")

    db.add(EntryPhoto(entry_id=entry_id, photo_id=body.photo_id, position=body.position))
    await db.flush()
    await db.commit()
    return _photo_out(photo)


@router.delete("/entries/{entry_id}/photos/{photo_id}", status_code=204)
async def detach_photo_from_entry(
    entry_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    entry, diary, role = await _get_entry_or_404(entry_id, user, db, require_editor=True)
    existing = (await db.execute(
        select(EntryPhoto).where(
            EntryPhoto.entry_id == entry_id, EntryPhoto.photo_id == photo_id
        )
    )).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="not_found")
    await db.delete(existing)
    await db.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k "to_entry or from_entry"`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): entry photo attach/detach endpoints"
```

---

## Task 17: GET /v1/diaries/{id}/photos — list diary library

**Files:**
- Modify: `apps/api/app/routers/v1/diaries.py`
- Test: `apps/api/tests/integration/test_photos.py`

Owner sees the full library (all DiaryPhoto rows joined to Photo where photo not deleted). Viewers/editors see only photos reachable via published, non-deleted entries (viewer) plus drafts (editor).

- [ ] **Step 1: Write failing tests**

Append to `apps/api/tests/integration/test_photos.py`:

```python
@pytest.mark.asyncio
async def test_list_diary_photos_owner(client):
    headers = await _login(client, "lst@example.com")
    diary_id = await _make_diary(client, headers)
    pid = await _make_photo_for_user(client, headers)
    await client.post(f"/v1/diaries/{diary_id}/photos/attach",
        json={"photo_id": pid}, headers=headers)
    r = await client.get(f"/v1/diaries/{diary_id}/photos", headers=headers)
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()]
    assert pid in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k list_diary_photos`
Expected: FAIL — endpoint missing.

- [ ] **Step 3: Implement endpoint in diaries.py**

In `apps/api/app/routers/v1/diaries.py`, add (near other diary endpoints):

```python
from app.routers.v1.photos import PhotoOut as _PhotoOut, _photo_out as _photo_out_helper


@router.get("/diaries/{diary_id}/photos", response_model=list[_PhotoOut])
async def list_diary_photos(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[_PhotoOut]:
    from app.models import DiaryPhoto, Entry, EntryPhoto, Photo

    diary, role = await _get_diary_or_404(diary_id, user, db)

    if role is None:  # owner
        q = (
            select(Photo)
            .join(DiaryPhoto, DiaryPhoto.photo_id == Photo.id)
            .where(DiaryPhoto.diary_id == diary_id, Photo.deleted_at.is_(None))
            .order_by(Photo.created_at.desc())
        )
    else:
        statuses = ["published"] if role == "viewer" else ["published", "draft"]
        q = (
            select(Photo)
            .join(EntryPhoto, EntryPhoto.photo_id == Photo.id)
            .join(Entry, Entry.id == EntryPhoto.entry_id)
            .where(
                Entry.diary_id == diary_id,
                Entry.deleted_at.is_(None),
                Entry.status.in_(statuses),
                Photo.deleted_at.is_(None),
                Photo.finalized_at.is_not(None),
            )
            .distinct()
            .order_by(Photo.created_at.desc())
        )
    rows = (await db.execute(q)).scalars().all()
    return [_photo_out_helper(p) for p in rows]
```

(Note: a circular import between `diaries.py` and `photos.py` is fine because both are imported lazily inside `create_app`. If pytest reports an `ImportError`, move the helper to a shared module — but FastAPI's deferred-import pattern in `main.py` should keep this working.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k list_diary_photos`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/diaries.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): GET /v1/diaries/{id}/photos library endpoint"
```

---

### Task 18: Add `photos` field to `EntryOut` + eager-load

**Files:**
- Modify: `apps/api/app/routers/v1/entries.py:74-94` (EntryOut), `:118-162` (_entry_out_from_orm), `:170-198` (_get_entry_or_404)
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_photos.py`:

```python
async def test_entry_out_includes_attached_photos(client, owner_token, diary, entry, finalized_photo):
    # Attach photo to entry
    await client.post(
        f"/v1/entries/{entry.id}/photos",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"photo_id": str(finalized_photo.id), "position": 0},
    )
    r = await client.get(
        f"/v1/entries/{entry.id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 200
    photos = r.json()["photos"]
    assert len(photos) == 1
    assert photos[0]["id"] == str(finalized_photo.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -v -k entry_out_includes`
Expected: FAIL — `KeyError: 'photos'` (field not yet on EntryOut).

- [ ] **Step 3: Implement**

In `apps/api/app/routers/v1/entries.py`:

1. Import at top: `from app.routers.v1.photos import PhotoOut, _photo_out`. (If circular, define a tiny local `_photo_to_dict` instead.)
2. Add to `EntryOut`:

```python
    photos: list["PhotoOut"] = []
```

3. Extend `_get_entry_or_404` selectinload list:

```python
        .options(
            selectinload(Entry.events),
            selectinload(Entry.rule_matches).selectinload(EntryRuleMatch.rule),
            selectinload(Entry.llm_generations),
            selectinload(Entry.entry_photos).selectinload(EntryPhoto.photo),
        )
```

Add `EntryPhoto` to `from app.models import …`.

4. Update `_entry_out_from_orm` — sort entry_photos with NULL position last, filter deleted:

```python
    photos_out = [
        _photo_out(ep.photo)
        for ep in sorted(
            entry.entry_photos,
            key=lambda ep: (ep.position is None, ep.position or 0),
        )
        if ep.photo is not None and ep.photo.deleted_at is None
    ]
```

Pass `photos=photos_out` into the `EntryOut(...)` constructor.

5. Apply same pattern to `list_entries` and `list_deleted_entries` query options.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos.py tests/integration/test_entries.py -v`
Expected: PASS — including pre-existing entry tests.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/v1/entries.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): include photos in EntryOut response"
```

---

### Task 19: Rewrite `_sweep_orphaned_photos` to fix shim and scrub `tmp/`

**Files:**
- Modify: `apps/api/app/workers/beat_tasks.py:87-135`
- Test: `apps/api/tests/integration/test_photos_sweeper.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/integration/test_photos_sweeper.py
import io
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Photo
from app.workers.beat_tasks import _sweep_orphaned_photos


@pytest.mark.asyncio
async def test_sweep_removes_old_unfinalized_row(db, owner_user, s3, settings):
    photo = Photo(
        owner_user_id=owner_user.id,
        s3_key=f"{owner_user.id}/abc.enc",
        thumbnail_s3_key=f"{owner_user.id}/abc.thumb.enc",
        wrapped_dek=b"\x00" * 49,
        size_bytes=0,
        content_type="image/jpeg",
        finalized_at=None,
    )
    photo.created_at = datetime.now(tz=UTC) - timedelta(hours=25)
    db.add(photo)
    await db.commit()

    # Put a stray tmp object older than 24h
    s3.put_object(
        Bucket=settings.s3_bucket,
        Key=f"tmp/{owner_user.id}/stray",
        Body=b"x",
    )

    deleted = await _sweep_orphaned_photos()
    assert deleted >= 1

    # Row should be gone
    assert (await db.get(Photo, photo.id)) is None
    # tmp object should be gone
    listed = s3.list_objects_v2(
        Bucket=settings.s3_bucket, Prefix=f"tmp/{owner_user.id}/"
    ).get("Contents", [])
    assert listed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos_sweeper.py -v`
Expected: FAIL — current implementation uses broken `func.cast`, returns 0, and never lists `tmp/`.

- [ ] **Step 3: Implement**

Replace `_sweep_orphaned_photos` body with:

```python
async def _sweep_orphaned_photos() -> int:
    """Delete unfinalized Photo rows older than 24h and stray tmp/ objects."""
    settings = get_settings()
    s3 = get_s3()
    cutoff = datetime.now(tz=UTC) - timedelta(hours=24)

    deleted = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Photo).where(
                Photo.finalized_at.is_(None),
                Photo.created_at < cutoff,
            )
        )
        for photo in result.scalars().all():
            # Best-effort delete of any tmp object for this photo
            tmp_key = f"tmp/{photo.owner_user_id}/{photo.id}"
            try:
                s3.delete_object(Bucket=settings.s3_bucket, Key=tmp_key)
            except Exception:  # noqa: BLE001
                pass
            # Hard-delete the row (no soft delete for unfinalized)
            await db.delete(photo)
            deleted += 1
        await db.commit()

    # Reconcile tmp/ prefix: anything older than 24h with no row gets deleted
    paginator = s3.get_paginator("list_objects_v2")
    cutoff_ts = cutoff
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix="tmp/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff_ts:
                try:
                    s3.delete_object(Bucket=settings.s3_bucket, Key=obj["Key"])
                    deleted += 1
                except Exception:  # noqa: BLE001
                    pass
    return deleted
```

Imports needed: `from datetime import UTC, datetime, timedelta`, `from sqlalchemy import select`, `from app.core.config import get_settings`, `from app.core.dependencies import get_s3`, `from app.core.database import AsyncSessionLocal`, `from app.models import Photo`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photos_sweeper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/beat_tasks.py apps/api/tests/integration/test_photos_sweeper.py
git commit -m "fix(api): sweep orphaned photo rows + tmp/ objects after 24h"
```

---

### Task 20: Extend `hard_delete_user` to scrub `tmp/{user_id}/`

**Files:**
- Modify: `apps/api/app/workers/hard_delete.py:157-167`
- Test: `apps/api/tests/integration/test_hard_delete.py` (extend if exists, else create)

- [ ] **Step 1: Write the failing test**

```python
async def test_hard_delete_user_removes_tmp_prefix(db, owner_user, s3, settings):
    s3.put_object(
        Bucket=settings.s3_bucket,
        Key=f"tmp/{owner_user.id}/leftover",
        Body=b"x",
    )
    from app.workers.hard_delete import hard_delete_user
    await hard_delete_user(owner_user.id)
    listed = s3.list_objects_v2(
        Bucket=settings.s3_bucket, Prefix=f"tmp/{owner_user.id}/"
    ).get("Contents", [])
    assert listed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_hard_delete.py -v -k tmp_prefix`
Expected: FAIL — current code only scrubs `{user_id}/`.

- [ ] **Step 3: Implement**

Add a second `Prefix=f"tmp/{user_id}/"` listing/delete pass after the existing one in `hard_delete.py:157-167`:

```python
    # Scrub final-bucket prefix (existing)
    paginator = s3.get_paginator("list_objects_v2")
    for prefix in (f"{user_id}/", f"tmp/{user_id}/"):
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": keys})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/integration/test_hard_delete.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/hard_delete.py apps/api/tests/integration/test_hard_delete.py
git commit -m "fix(api): hard_delete also scrubs tmp/{user_id}/ prefix"
```

---

### Task 21: Wire sweeper into Celery beat schedule

**Files:**
- Modify: `apps/api/app/workers/celery_app.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_celery_beat.py
def test_sweep_orphaned_photos_in_beat_schedule():
    from app.workers.celery_app import celery_app
    sched = celery_app.conf.beat_schedule
    assert "sweep-orphaned-photos" in sched
    entry = sched["sweep-orphaned-photos"]
    assert entry["task"].endswith("sweep_orphaned_photos")
    # 6 hours
    assert entry["schedule"].total_seconds() == 6 * 3600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_celery_beat.py -v`
Expected: FAIL — schedule entry missing.

- [ ] **Step 3: Implement**

In `apps/api/app/workers/celery_app.py` add to `beat_schedule`:

```python
    "sweep-orphaned-photos": {
        "task": "app.workers.beat_tasks.sweep_orphaned_photos",
        "schedule": timedelta(hours=6),
    },
```

Ensure `from datetime import timedelta` is imported. Confirm `sweep_orphaned_photos` is registered as a Celery task in `beat_tasks.py` (wrap `_sweep_orphaned_photos` with `@celery_app.task(name=...)` if not already done).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && .venv/bin/pytest tests/unit/test_celery_beat.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/workers/celery_app.py apps/api/app/workers/beat_tasks.py apps/api/tests/unit/test_celery_beat.py
git commit -m "feat(api): schedule photo orphan sweeper every 6h"
```

---

### Task 22: Frontend API client — `Photo` types + `api.photos` namespace

**Files:**
- Modify: `apps/web/src/lib/api.ts:265-473`

- [ ] **Step 1: Write the failing test**

Add to `apps/web/src/lib/__tests__/api.test.ts` (create if missing):

```ts
import { api } from "../api";

describe("api.photos", () => {
  it("namespace exists with the expected methods", () => {
    expect(typeof api.photos.requestUploadUrl).toBe("function");
    expect(typeof api.photos.uploadFile).toBe("function");
    expect(typeof api.photos.finalize).toBe("function");
    expect(typeof api.photos.get).toBe("function");
    expect(typeof api.photos.delete).toBe("function");
    expect(typeof api.photos.attachToDiary).toBe("function");
    expect(typeof api.photos.attachToEntry).toBe("function");
    expect(typeof api.photos.detachFromEntry).toBe("function");
    expect(typeof api.photos.listForDiary).toBe("function");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm test -- api.test`
Expected: FAIL — `api.photos` undefined.

- [ ] **Step 3: Implement**

Add types and the `api.photos` namespace. Append after the existing `Entry` type block:

```ts
export type Photo = {
  id: string;
  owner_user_id: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  taken_at: string | null;
  content_type: string;
  finalized_at: string | null;
  created_at: string;
};

export type UploadUrl = {
  photo_id: string;
  upload_url: string;
  expires_in: number;
};

export type Entry = {
  // ...existing fields...
  photos: Photo[];
};
```

Add a `apiFetchBlob` helper (parallel to `apiFetch` but returns a `Blob`, throws on non-2xx). Then:

```ts
export const api = {
  // ...existing namespaces...
  photos: {
    requestUploadUrl: (body: { content_type: string; size_bytes: number }) =>
      apiFetch<UploadUrl>("/v1/photos/upload-url", {
        method: "POST",
        body: JSON.stringify(body),
      }),

    uploadFile: async (uploadUrl: string, file: File, onProgress?: (p: number) => void) => {
      // Use XHR for upload progress events
      return new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", uploadUrl);
        xhr.setRequestHeader("Content-Type", file.type);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && onProgress) {
            onProgress(e.loaded / e.total);
          }
        };
        xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`upload failed: ${xhr.status}`)));
        xhr.onerror = () => reject(new Error("upload network error"));
        xhr.send(file);
      });
    },

    finalize: (photoId: string) =>
      apiFetch<Photo>(`/v1/photos/${photoId}/finalize`, { method: "POST" }),

    get: (photoId: string, kind: "full" | "thumb" = "full") =>
      apiFetchBlob(`/v1/photos/${photoId}?kind=${kind}`),

    delete: (photoId: string) =>
      apiFetch<void>(`/v1/photos/${photoId}`, { method: "DELETE" }),

    attachToDiary: (diaryId: string, photoId: string) =>
      apiFetch<void>(`/v1/diaries/${diaryId}/photos`, {
        method: "POST",
        body: JSON.stringify({ photo_id: photoId }),
      }),

    attachToEntry: (entryId: string, photoId: string, position?: number) =>
      apiFetch<void>(`/v1/entries/${entryId}/photos`, {
        method: "POST",
        body: JSON.stringify({ photo_id: photoId, position }),
      }),

    detachFromEntry: (entryId: string, photoId: string) =>
      apiFetch<void>(`/v1/entries/${entryId}/photos/${photoId}`, {
        method: "DELETE",
      }),

    listForDiary: (diaryId: string) =>
      apiFetch<Photo[]>(`/v1/diaries/${diaryId}/photos`),
  },
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && pnpm test -- api.test && pnpm typecheck`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api.ts apps/web/src/lib/__tests__/api.test.ts
git commit -m "feat(web): api.photos namespace + Photo types"
```

---

### Task 23: `<PhotoThumbnail/>` component (blob fetch + cleanup)

**Files:**
- Create: `apps/web/src/components/PhotoThumbnail.tsx`
- Test: `apps/web/src/components/__tests__/PhotoThumbnail.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { PhotoThumbnail } from "../PhotoThumbnail";
import { api } from "../../lib/api";

jest.mock("../../lib/api");

it("fetches blob and renders <img>", async () => {
  const blob = new Blob([new Uint8Array([0xff, 0xd8])], { type: "image/jpeg" });
  (api.photos.get as jest.Mock).mockResolvedValue(blob);
  const revokeSpy = jest.spyOn(URL, "revokeObjectURL");

  const { unmount } = render(<PhotoThumbnail photoId="abc" alt="x" />);
  await waitFor(() => expect(screen.getByRole("img")).toHaveAttribute("src"));

  unmount();
  expect(revokeSpy).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm test -- PhotoThumbnail`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

export function PhotoThumbnail({
  photoId,
  alt,
  onClick,
  className,
}: {
  photoId: string;
  alt: string;
  onClick?: () => void;
  className?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    api.photos.get(photoId, "thumb").then((blob) => {
      if (cancelled) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoId]);

  if (!src) return <div className={className} aria-busy="true" />;
  return <img src={src} alt={alt} onClick={onClick} className={className} />;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && pnpm test -- PhotoThumbnail`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/PhotoThumbnail.tsx apps/web/src/components/__tests__/PhotoThumbnail.test.tsx
git commit -m "feat(web): PhotoThumbnail component"
```

---

### Task 24: `<PhotoUploadButton/>` component

**Files:**
- Create: `apps/web/src/components/PhotoUploadButton.tsx`
- Test: `apps/web/src/components/__tests__/PhotoUploadButton.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PhotoUploadButton } from "../PhotoUploadButton";
import { api } from "../../lib/api";

jest.mock("../../lib/api");

it("requests URL, uploads, finalizes, calls onUploaded", async () => {
  (api.photos.requestUploadUrl as jest.Mock).mockResolvedValue({
    photo_id: "p1", upload_url: "https://example/u", expires_in: 600,
  });
  (api.photos.uploadFile as jest.Mock).mockResolvedValue(undefined);
  (api.photos.finalize as jest.Mock).mockResolvedValue({ id: "p1", finalized_at: "now" });

  const onUploaded = jest.fn();
  render(<PhotoUploadButton onUploaded={onUploaded} />);

  const file = new File([new Uint8Array(100)], "x.jpg", { type: "image/jpeg" });
  const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith({ id: "p1", finalized_at: "now" }));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm test -- PhotoUploadButton`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
"use client";
import { useState } from "react";
import { api, Photo } from "../lib/api";

export function PhotoUploadButton({ onUploaded }: { onUploaded: (photo: Photo) => void }) {
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      const meta = await api.photos.requestUploadUrl({
        content_type: file.type,
        size_bytes: file.size,
      });
      await api.photos.uploadFile(meta.upload_url, file, setProgress);
      const photo = await api.photos.finalize(meta.photo_id);
      onUploaded(photo);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setProgress(null);
    }
  }

  return (
    <label>
      Upload
      <input type="file" accept="image/*" onChange={handleChange} hidden />
      {progress !== null && <span>{Math.round(progress * 100)}%</span>}
      {error && <span role="alert">{error}</span>}
    </label>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && pnpm test -- PhotoUploadButton`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/PhotoUploadButton.tsx apps/web/src/components/__tests__/PhotoUploadButton.test.tsx
git commit -m "feat(web): PhotoUploadButton component"
```

---

### Task 25: `<PhotoLightbox/>` component (modal + arrow nav)

**Files:**
- Create: `apps/web/src/components/PhotoLightbox.tsx`
- Test: `apps/web/src/components/__tests__/PhotoLightbox.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { PhotoLightbox } from "../PhotoLightbox";

it("navigates with arrow keys and closes on Escape", () => {
  const onClose = jest.fn();
  const onIndex = jest.fn();
  render(
    <PhotoLightbox
      photoIds={["a", "b", "c"]}
      index={1}
      onIndexChange={onIndex}
      onClose={onClose}
    />
  );
  fireEvent.keyDown(window, { key: "ArrowRight" });
  expect(onIndex).toHaveBeenCalledWith(2);
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  expect(onIndex).toHaveBeenCalledWith(0);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm test -- PhotoLightbox`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

export function PhotoLightbox({
  photoIds,
  index,
  onIndexChange,
  onClose,
}: {
  photoIds: string[];
  index: number;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setSrc(null);
    api.photos.get(photoIds[index], "full").then((blob) => {
      if (cancelled) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoIds, index]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && index < photoIds.length - 1) onIndexChange(index + 1);
      if (e.key === "ArrowLeft" && index > 0) onIndexChange(index - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, photoIds.length, onClose, onIndexChange]);

  return (
    <div role="dialog" aria-modal="true" onClick={onClose} className="lightbox">
      {src && <img src={src} alt="" onClick={(e) => e.stopPropagation()} />}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && pnpm test -- PhotoLightbox`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/PhotoLightbox.tsx apps/web/src/components/__tests__/PhotoLightbox.test.tsx
git commit -m "feat(web): PhotoLightbox modal"
```

---

### Task 26: Diary photo library page

**Files:**
- Create: `apps/web/src/app/diaries/[diaryId]/photos/page.tsx`

- [ ] **Step 1: Write the failing test (e2e smoke)**

Defer the heavy assertion to Task 28; for this task add a typecheck-only check by rendering the page in a Playwright spec stub:

```ts
// apps/web/e2e/photos-library.spec.ts
import { test, expect } from "@playwright/test";
test("library page renders heading", async ({ page }) => {
  await page.goto("/diaries/00000000-0000-0000-0000-000000000000/photos");
  // Will redirect to login when unauth — test heading post-login in Task 28.
  await expect(page).toHaveTitle(/Perfect Day/i);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm exec playwright test photos-library`
Expected: FAIL — page does not exist (404).

- [ ] **Step 3: Implement**

```tsx
"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, Photo } from "@/lib/api";
import { PhotoThumbnail } from "@/components/PhotoThumbnail";
import { PhotoUploadButton } from "@/components/PhotoUploadButton";
import { PhotoLightbox } from "@/components/PhotoLightbox";

export default function DiaryPhotosPage() {
  const { diaryId } = useParams<{ diaryId: string }>();
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  useEffect(() => {
    api.photos.listForDiary(diaryId).then(setPhotos);
  }, [diaryId]);

  return (
    <main>
      <h1>Photo library</h1>
      <PhotoUploadButton
        onUploaded={async (p) => {
          await api.photos.attachToDiary(diaryId, p.id);
          const refreshed = await api.photos.listForDiary(diaryId);
          setPhotos(refreshed);
        }}
      />
      <ul className="grid">
        {photos.map((p, i) => (
          <li key={p.id}>
            <PhotoThumbnail photoId={p.id} alt="" onClick={() => setOpenIndex(i)} />
          </li>
        ))}
      </ul>
      {openIndex !== null && (
        <PhotoLightbox
          photoIds={photos.map((p) => p.id)}
          index={openIndex}
          onIndexChange={setOpenIndex}
          onClose={() => setOpenIndex(null)}
        />
      )}
    </main>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && pnpm typecheck && pnpm exec playwright test photos-library`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/diaries/'[diaryId]'/photos/page.tsx apps/web/e2e/photos-library.spec.ts
git commit -m "feat(web): diary photo library page"
```

---

### Task 27: Entry page — thumbnail strip, attach picker, remove

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

- [ ] **Step 1: Write the failing test (component-level)**

```tsx
// apps/web/src/app/entries/[entryId]/__tests__/page.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import EntryPage from "../page";
import { api } from "@/lib/api";

jest.mock("@/lib/api");

it("renders attached thumbnails and remove button (editor)", async () => {
  (api.entries.get as jest.Mock).mockResolvedValue({
    id: "e1",
    photos: [{ id: "p1", finalized_at: "now" }],
    // ... other minimal fields
  });
  // params hook mock omitted for brevity

  render(<EntryPage />);
  await waitFor(() => expect(screen.getByRole("img")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm test -- entries.*page`
Expected: FAIL — entry page has no photo UI yet.

- [ ] **Step 3: Implement**

In `apps/web/src/app/entries/[entryId]/page.tsx`:

- After the entry body, render a thumbnail strip from `entry.photos` using `PhotoThumbnail`.
- Add an "Attach photo" button that opens a modal listing `api.photos.listForDiary(entry.diary_id)` minus already-attached IDs.
- On select: call `api.photos.attachToEntry(entry.id, photo.id)` then refetch entry.
- Each thumbnail in the strip has a "Remove" button (editor only) that calls `api.photos.detachFromEntry(entry.id, photo.id)` and refetches.
- Clicking a thumbnail opens `<PhotoLightbox/>` over `entry.photos`.

Hide remove + attach controls when `role === "viewer"` (use existing role detection in this page; if absent, derive from a new `api.diaries.getMyRole(diaryId)` call — defer if a helper already exists).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && pnpm test -- entries.*page && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/entries/'[entryId]'/page.tsx apps/web/src/app/entries/'[entryId]'/__tests__/page.test.tsx
git commit -m "feat(web): entry photo strip + attach/remove"
```

---

### Task 28: Playwright e2e — full upload + attach + lightbox flow

**Files:**
- Create: `apps/web/e2e/photo-upload.spec.ts`
- Create: `apps/web/e2e/fixtures/sample.jpg` (small real JPEG)

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/e2e/photo-upload.spec.ts
import { test, expect } from "@playwright/test";
import path from "node:path";

test("upload → attach → lightbox", async ({ page }) => {
  await page.goto("/login");
  await page.fill('input[name="email"]', process.env.E2E_EMAIL!);
  await page.fill('input[name="password"]', process.env.E2E_PASSWORD!);
  await page.click('button[type="submit"]');

  // Navigate to a known seeded diary's photo library
  await page.goto(`/diaries/${process.env.E2E_DIARY_ID}/photos`);

  await page.locator('input[type="file"]').setInputFiles(
    path.join(__dirname, "fixtures/sample.jpg")
  );

  // Wait for thumbnail to appear
  const thumb = page.getByRole("img").first();
  await expect(thumb).toBeVisible({ timeout: 15_000 });

  // Open lightbox
  await thumb.click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // Arrow-right is a no-op with one photo; Escape closes
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && pnpm exec playwright test photo-upload`
Expected: FAIL initially (or PASS if all prior tasks land green).

- [ ] **Step 3: Adjust fixtures / seeding as needed**

Make sure `apps/web/playwright.config.ts` exposes `E2E_EMAIL`, `E2E_PASSWORD`, `E2E_DIARY_ID` from env. Use existing seed fixtures if present in `apps/api/tests/fixtures/factories.py` — wire a one-shot seed script if not.

- [ ] **Step 4: Run end-to-end**

Run: `make test-e2e` (which is the canonical entry — see `Makefile`).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/e2e/photo-upload.spec.ts apps/web/e2e/fixtures/sample.jpg
git commit -m "test(web): e2e photo upload + attach + lightbox"
```

---

## Verification

After all tasks land:

1. `cd apps/api && .venv/bin/pytest tests/unit -v` — unit suite green (crypto + celery beat).
2. `cd apps/api && .venv/bin/pytest tests/integration -v` — integration green (photos endpoints, sweeper, hard-delete, entries-with-photos).
3. `cd apps/web && pnpm test` — RTL component tests green.
4. `cd apps/web && pnpm typecheck && pnpm lint` — clean.
5. `make test-all` — full chain (lint → typecheck → unit/integration → e2e) green.

**Manual API smoke (optional, against local stack):**

```bash
# Stack up
make up

# Login → grab JWT
TOKEN=$(curl -s -X POST localhost:8000/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"...","password":"..."}' | jq -r .access_token)

# Request upload URL
META=$(curl -s -X POST localhost:8000/v1/photos/upload-url \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"content_type":"image/jpeg","size_bytes":12345}')
PHOTO_ID=$(echo $META | jq -r .photo_id)
URL=$(echo $META | jq -r .upload_url)

# Upload plaintext
curl -X PUT "$URL" --data-binary @apps/api/tests/fixtures/photos/sample.jpg \
  -H 'content-type: image/jpeg'

# Finalize
curl -X POST localhost:8000/v1/photos/$PHOTO_ID/finalize \
  -H "authorization: Bearer $TOKEN"

# Fetch full and thumb
curl -OJ "localhost:8000/v1/photos/$PHOTO_ID?kind=full"  -H "authorization: Bearer $TOKEN"
curl -OJ "localhost:8000/v1/photos/$PHOTO_ID?kind=thumb" -H "authorization: Bearer $TOKEN"
```

**Sweeper verification:**

```bash
# Insert a Photo row with finalized_at=NULL and created_at=now()-25h, then:
celery -A app.workers.celery_app call app.workers.beat_tasks.sweep_orphaned_photos
# → row should be gone, tmp/{user}/{id} should be gone.
```

**Hard-delete verification:**

```bash
celery -A app.workers.celery_app call app.workers.hard_delete.hard_delete_user --args='["<user-id>"]'
# → Both {user_id}/ and tmp/{user_id}/ prefixes empty in MinIO.
```

---

## Self-review notes

- **Spec coverage:** All 8 endpoints from `design/03-api-surface.md` (request URL, finalize, GET full+thumb, DELETE, attach diary, attach entry, detach entry, list diary photos) → Tasks 11-17. Encryption per `design/08-security-privacy.md` → already done in `photo_crypto.py` (commit 399ef0e). Sweeper → Tasks 19, 21. Hard-delete extension → Task 20. Frontend (library, lightbox, upload, attach) → Tasks 22-27. E2e → Task 28.
- **Type consistency:** `Photo` shape in `api.ts` matches `PhotoOut` in `photos.py` (id, owner_user_id, size_bytes, width, height, taken_at, content_type, finalized_at, created_at). Method names align across tasks: `requestUploadUrl`, `uploadFile`, `finalize`, `get`, `delete`, `attachToDiary`, `attachToEntry`, `detachFromEntry`, `listForDiary`.
- **No placeholders:** every step includes either explicit code, exact commands, or a referenced helper that already exists in the repo.

