"""Photo upload, encryption, retrieval, and attachment endpoints."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.photo_crypto import encrypt_stream, generate_dek, wrap_dek
from app.models import Photo, User
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
