"""Turn image bytes into an encrypted, finalized Photo row.

Used by the upload endpoint and (later) the Google Photos import worker, so it
takes no request objects and never commits: the caller owns the transaction.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from functools import partial
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.photo_crypto import (
    PURPOSE_DISPLAY,
    PURPOSE_THUMB,
    encrypt_stream,
    generate_dek,
    wrap_dek,
)
from app.models import Photo, User
from app.services.photos import (
    ALLOWED_MIME,
    delete_object,
    detect_mime,
    generate_thumbnail,
    make_display_jpeg,
    parse_exif,
    put_object_bytes,
)


class UnsupportedPhoto(Exception):  # noqa: N818 — named by the task interface
    """The bytes are not an allowed image type."""


async def ingest_photo(
    db: AsyncSession,
    *,
    owner: User,
    data: bytes,
    source: Literal["upload", "google_photos"],
    external_id: str | None = None,
    taken_at_fallback: datetime | None = None,
) -> Photo:
    """Validate, thumbnail, encrypt and store *data*; add a finalized Photo and flush."""
    mime = detect_mime(data[:32])
    if mime not in ALLOWED_MIME:
        raise UnsupportedPhoto(mime or "unknown")

    loop = asyncio.get_running_loop()
    exif = parse_exif(data)
    try:
        thumb = await loop.run_in_executor(None, lambda: generate_thumbnail(data, mime))
    except ValueError:
        thumb = b""  # non-fatal: store without a thumbnail

    try:
        display = await loop.run_in_executor(None, lambda: make_display_jpeg(data, mime))
    except ValueError:
        display = None  # non-fatal: the backfill sweep retries

    dek = generate_dek()
    wrapped = wrap_dek(dek, owner.id)
    enc_full = await loop.run_in_executor(None, lambda: encrypt_stream(data, dek))
    enc_thumb = (
        await loop.run_in_executor(None, lambda: encrypt_stream(thumb, dek, PURPOSE_THUMB))
        if thumb
        else None
    )

    enc_display = (
        await loop.run_in_executor(None, lambda: encrypt_stream(display, dek, PURPOSE_DISPLAY))
        if display
        else None
    )

    photo_id = uuid.uuid4()
    full_key = f"{owner.id}/{photo_id}.enc"
    thumb_key = f"{owner.id}/{photo_id}_thumb.enc"
    display_key = f"{owner.id}/{photo_id}_display.enc"
    stored: list[str] = []
    try:
        await loop.run_in_executor(None, lambda: put_object_bytes(full_key, enc_full))
        stored.append(full_key)
        if enc_thumb:
            await loop.run_in_executor(None, lambda: put_object_bytes(thumb_key, enc_thumb))
            stored.append(thumb_key)
        if enc_display:
            await loop.run_in_executor(None, lambda: put_object_bytes(display_key, enc_display))
            stored.append(display_key)
        photo = Photo(
            id=photo_id,
            user_id=owner.id,
            s3_key=full_key,
            mime_type=mime,
            bytes=len(data),
            taken_at=exif.get("taken_at") or taken_at_fallback,
            lat=exif.get("lat"),
            lon=exif.get("lon"),
            source=source,
            external_id=external_id,
            thumbnail_s3_key=thumb_key if enc_thumb else None,
            display_s3_key=display_key if enc_display else None,
            dek_ciphertext=wrapped,
            finalized_at=datetime.now(tz=UTC),
        )
        db.add(photo)
        await db.flush()
    except Exception:
        for key in stored:  # don't leave orphans behind a failed row
            try:
                await loop.run_in_executor(None, partial(delete_object, key))
            except Exception:  # noqa: BLE001, S110 — the orphan sweeper is the backstop
                pass
        raise
    return photo
