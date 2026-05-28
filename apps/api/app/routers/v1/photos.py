"""Photo upload, encryption, retrieval, and attachment endpoints."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.photo_crypto import encrypt_stream, generate_dek, iter_decrypt_stream, unwrap_dek, wrap_dek
from app.models import DiaryPhoto, Photo, User
from app.routers.v1.diaries import _get_diary_or_404
from app.services.photos import (
    ALLOWED_MIME,
    MAX_BYTES,
    PRESIGN_TTL_SECONDS,
    delete_object,
    detect_mime,
    generate_thumbnail,
    get_object_bytes,
    parse_exif,
    presign_put_url,
    put_object_bytes,
    stream_object,
)

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


@router.post("/photos/{photo_id}/finalize", response_model=PhotoOut)
async def finalize_photo(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PhotoOut:
    # Look up photo owned by this user
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

    # Idempotent: already finalized
    if photo.finalized_at is not None:
        return _photo_out(photo)

    tmp_key = f"tmp/{user.id}/{photo_id}"

    # Fetch plaintext from tmp/
    loop = asyncio.get_event_loop()
    try:
        plaintext = await loop.run_in_executor(None, lambda: get_object_bytes(tmp_key))
    except Exception:
        raise HTTPException(status_code=422, detail="tmp_not_found")

    # Sniff MIME from actual bytes
    detected = detect_mime(plaintext[:32])
    if detected not in ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="unsupported_mime")

    # EXIF
    exif = parse_exif(plaintext)

    # Thumbnail
    try:
        thumb_bytes = await loop.run_in_executor(
            None, lambda: generate_thumbnail(plaintext, detected)
        )
    except ValueError:
        thumb_bytes = b""  # non-fatal: store without thumbnail

    # Encrypt
    dek = generate_dek()
    wrapped_dek = wrap_dek(dek, user.id)
    enc_full = await loop.run_in_executor(None, lambda: encrypt_stream(plaintext, dek))
    enc_thumb = (
        await loop.run_in_executor(None, lambda: encrypt_stream(thumb_bytes, dek))
        if thumb_bytes
        else None
    )

    # Write to final keys
    full_key = photo.s3_key  # already set as {user_id}/{photo_id}.enc
    thumb_key = full_key.replace(".enc", "_thumb.enc")
    await loop.run_in_executor(None, lambda: put_object_bytes(full_key, enc_full))
    if enc_thumb:
        await loop.run_in_executor(None, lambda: put_object_bytes(thumb_key, enc_thumb))

    # Delete tmp (best-effort)
    try:
        await loop.run_in_executor(None, lambda: delete_object(tmp_key))
    except Exception:
        pass

    # Update row
    photo.mime_type = detected
    photo.bytes = len(plaintext)
    photo.taken_at = exif.get("taken_at")
    photo.lat = exif.get("lat")
    photo.lon = exif.get("lon")
    photo.dek_ciphertext = wrapped_dek
    photo.thumbnail_s3_key = thumb_key if enc_thumb else None
    photo.finalized_at = datetime.now(tz=UTC)

    return _photo_out(photo)


# ---------------------------------------------------------------------------
# Task 12: GET /v1/photos/{id}?kind=full|thumb
# ---------------------------------------------------------------------------


@router.get("/photos/{photo_id}")
async def get_photo(
    photo_id: uuid.UUID,
    kind: Literal["full", "thumb"] = "full",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Photo).where(
            Photo.id == photo_id,
            Photo.user_id == user.id,
            Photo.deleted_at.is_(None),
            Photo.finalized_at.is_not(None),
        )
    )
    photo = result.scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Determine which S3 key to use; fall back to full when thumb is absent
    if kind == "thumb" and photo.thumbnail_s3_key is not None:
        key = photo.thumbnail_s3_key
        media_type = "image/jpeg"
    else:
        key = photo.s3_key
        media_type = photo.mime_type or "application/octet-stream"

    dek = unwrap_dek(photo.dek_ciphertext, user.id)

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

        yield from iter_decrypt_stream(exact_read, dek)

    return StreamingResponse(_stream(), media_type=media_type)


# ---------------------------------------------------------------------------
# Task 13: DELETE /v1/photos/{id}
# ---------------------------------------------------------------------------


@router.delete("/photos/{photo_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_photo(
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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


# ---------------------------------------------------------------------------
# Task 14: Diary photo attach/detach
# ---------------------------------------------------------------------------


@router.post("/diaries/{diary_id}/photos", status_code=http_status.HTTP_201_CREATED)
async def attach_photo_to_diary(
    diary_id: uuid.UUID,
    body: PhotoAttachRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PhotoOut:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    # Verify photo ownership
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

    # Idempotent attach
    existing = await db.execute(
        select(DiaryPhoto).where(
            DiaryPhoto.diary_id == diary_id,
            DiaryPhoto.photo_id == body.photo_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(DiaryPhoto(diary_id=diary_id, photo_id=body.photo_id))
        await db.flush()

    return _photo_out(photo)


@router.delete(
    "/diaries/{diary_id}/photos/{photo_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def detach_photo_from_diary(
    diary_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role == "viewer":
        raise HTTPException(status_code=403, detail="forbidden")

    result = await db.execute(
        select(DiaryPhoto).where(
            DiaryPhoto.diary_id == diary_id,
            DiaryPhoto.photo_id == photo_id,
        )
    )
    dp = result.scalar_one_or_none()
    if dp is not None:
        await db.delete(dp)
