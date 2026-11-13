"""Photo upload, encryption, retrieval, and attachment endpoints."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi import status as http_status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.photo_crypto import (
    PURPOSE_FULL,
    PURPOSE_THUMB,
    iter_decrypt_stream,
    unwrap_dek,
)
from app.models import Diary, DiaryPermission, Entry, EntryPhoto, Photo, User
from app.services.photo_ingest import UnsupportedPhoto, ingest_photo
from app.services.photos import MAX_BYTES, stream_object, strip_location

logger = logging.getLogger(__name__)
router = APIRouter(tags=["photos"])


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


_CHUNK = 1024 * 1024


@router.post("/photos", response_model=PhotoOut, status_code=201)
async def upload_photo(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PhotoOut:
    """Receive the image, encrypt it, then store it: nothing is ever written in plaintext."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK):
        total += len(chunk)
        if total > MAX_BYTES:
            raise HTTPException(status_code=413, detail="size_exceeds_limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=415, detail="unsupported_mime")
    try:
        photo = await ingest_photo(db, owner=user, data=data, source="upload")
    except UnsupportedPhoto:
        raise HTTPException(status_code=415, detail="unsupported_mime") from None
    return _photo_out(photo)


# ---------------------------------------------------------------------------
# Task 18: GET /v1/photos — user-global photo library
# ---------------------------------------------------------------------------


@router.get("/photos", response_model=list[PhotoOut])
async def list_user_photos(
    near: date | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[PhotoOut]:
    q = (
        select(Photo)
        .where(
            Photo.user_id == user.id,
            Photo.deleted_at.is_(None),
            Photo.finalized_at.is_not(None),
        )
    )
    if near is None:
        q = q.order_by(Photo.created_at.desc())
    else:
        # Closest to the entry's date first; photos with no date after those.
        gap = func.abs(func.date(Photo.taken_at) - near)
        q = q.order_by(Photo.taken_at.is_(None), gap.asc(), Photo.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return [_photo_out(p) for p in rows]


# ---------------------------------------------------------------------------
# Task 12: GET /v1/photos/{id}?kind=full|thumb
# ---------------------------------------------------------------------------


async def _readable_photo(photo_id: uuid.UUID, user: User, db: AsyncSession) -> Photo | None:
    """The photo if *user* may read it, else None (design/03 "Photo authorization").

    Readable when the user uploaded it, or when it is attached to a live entry
    in a live diary the user can read: owner and editors always, viewers only
    once the entry is published.
    """
    role_ok = or_(
        Diary.owner_user_id == user.id,
        DiaryPermission.role == "editor",
        and_(DiaryPermission.role == "viewer", Entry.status == "published"),
    )
    shared = exists(
        select(EntryPhoto.photo_id)
        .join(Entry, Entry.id == EntryPhoto.entry_id)
        .join(Diary, Diary.id == Entry.diary_id)
        .outerjoin(
            DiaryPermission,
            and_(DiaryPermission.diary_id == Diary.id, DiaryPermission.user_id == user.id),
        )
        .where(
            EntryPhoto.photo_id == Photo.id,
            Entry.deleted_at.is_(None),
            Diary.deleted_at.is_(None),
            role_ok,
        )
    )
    result = await db.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.deleted_at.is_(None),
            Photo.finalized_at.is_not(None),
            or_(Photo.user_id == user.id, shared),
        )
    )
    return result.scalar_one_or_none()


@router.get("/photos/{photo_id}")
async def get_photo(
    photo_id: uuid.UUID,
    kind: Literal["full", "thumb"] = "full",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    photo = await _readable_photo(photo_id, user, db)
    if photo is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Determine which S3 key to use; fall back to full when thumb is absent
    if kind == "thumb" and photo.thumbnail_s3_key is not None:
        key = photo.thumbnail_s3_key
        media_type = "image/jpeg"
        purpose = PURPOSE_THUMB
    else:
        key = photo.s3_key
        media_type = photo.mime_type or "application/octet-stream"
        purpose = PURPOSE_FULL

    if photo.dek_ciphertext is None:
        raise HTTPException(status_code=409, detail="not_finalized")
    dek = unwrap_dek(photo.dek_ciphertext, photo.user_id)

    if purpose == PURPOSE_FULL and photo.user_id != user.id:
        # Family sees the picture, not where it was taken (S-12).
        def _read_all() -> bytes:
            s3_body = stream_object(key)

            def exact_read(n: int) -> bytes:
                buf = b""
                while len(buf) < n:
                    chunk = s3_body.read(n - len(buf))
                    if not chunk:
                        break
                    buf += chunk
                return buf

            return b"".join(iter_decrypt_stream(exact_read, dek, purpose))

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _read_all)
        try:
            data = await loop.run_in_executor(
                None, lambda: strip_location(data, photo.mime_type or "")
            )
        except ValueError:
            logger.warning("photo %s could not be re-saved without location", photo.id)
            raise HTTPException(status_code=409, detail="unreadable_photo")
        return Response(data, media_type=media_type)

    def _stream():
        s3_body = stream_object(key)

        def exact_read(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = s3_body.read(n - len(buf))
                if not chunk:
                    break
                buf += chunk
            return buf

        yield from iter_decrypt_stream(exact_read, dek, purpose)

    return StreamingResponse(_stream(), media_type=media_type)


# ---------------------------------------------------------------------------
# Task 13: DELETE /v1/photos/{id}
# ---------------------------------------------------------------------------


@router.delete("/photos/{photo_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_photo(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    result = await db.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.user_id == user.id,
            Photo.deleted_at.is_(None),
        )
    )
    photo = result.scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail="not_found")
    photo.deleted_at = datetime.now(tz=UTC)

    # Entries that used it may have taken their place from its GPS (item 24).
    entry_ids = (
        await db.execute(select(EntryPhoto.entry_id).where(EntryPhoto.photo_id == photo_id))
    ).scalars().all()
    if entry_ids:
        await db.commit()  # so the worker sees the photo gone
        from app.workers.tasks import queue_location_refresh

        for entry_id in entry_ids:
            queue_location_refresh(entry_id)


# ---------------------------------------------------------------------------
# Task 15: Entry photo attach/detach
# ---------------------------------------------------------------------------


@router.post("/entries/{entry_id}/photos", status_code=http_status.HTTP_201_CREATED)
async def attach_photo_to_entry(
    entry_id: uuid.UUID,
    body: PhotoAttachRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PhotoOut:
    from app.routers.v1.entries import _get_entry_or_404

    # require_editor=True rejects viewers with 403
    entry, diary, role = await _get_entry_or_404(entry_id, user, db, require_editor=True)

    # Verify photo ownership and finalized
    result = await db.execute(
        select(Photo).where(
            Photo.id == body.photo_id,
            Photo.user_id == user.id,
            Photo.deleted_at.is_(None),
            Photo.finalized_at.is_not(None),
        )
    )
    photo = result.scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail="photo_not_found")

    # Idempotent attach — if already linked, optionally update position; else insert
    existing_result = await db.execute(
        select(EntryPhoto).where(
            EntryPhoto.entry_id == entry_id,
            EntryPhoto.photo_id == body.photo_id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is None:
        ep = EntryPhoto(entry_id=entry_id, photo_id=body.photo_id, position=body.position)
        db.add(ep)
        await db.flush()
    elif body.position is not None:
        existing.position = body.position

    if existing is None:
        # A photo may carry GPS: the entry's place (and weather) may change.
        # Commit first so the worker sees the new link.
        await db.commit()
        from app.workers.tasks import queue_location_refresh

        queue_location_refresh(entry_id)
    return _photo_out(photo)


@router.delete(
    "/entries/{entry_id}/photos/{photo_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def detach_photo_from_entry(
    entry_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    from app.routers.v1.entries import _get_entry_or_404

    entry, diary, role = await _get_entry_or_404(entry_id, user, db, require_editor=True)

    result = await db.execute(
        select(EntryPhoto).where(
            EntryPhoto.entry_id == entry_id,
            EntryPhoto.photo_id == photo_id,
        )
    )
    ep = result.scalar_one_or_none()
    if ep is not None:
        await db.delete(ep)
        await db.commit()  # so the worker sees the link gone
        from app.workers.tasks import queue_location_refresh

        queue_location_refresh(entry_id)


# ---------------------------------------------------------------------------
# Task 16: GET /v1/photos/{id}/metadata
# ---------------------------------------------------------------------------


@router.get("/photos/{photo_id}/metadata", response_model=PhotoOut)
async def get_photo_metadata(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PhotoOut:
    result = await db.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.user_id == user.id,
            Photo.deleted_at.is_(None),
        )
    )
    photo = result.scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail="not_found")
    return _photo_out(photo)
