"""Picking photos from Google Photos onto an entry (item 14).

Starting an import creates a Google Photos Picker session and queues the
``process_photo_import`` worker, which polls Google and copies the picks into
the caller's own library. The page only reads the import's outcome; the
worker finishes without it.

Responses never carry Google tokens or media URLs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import PhotoImport, User
from app.services import google_photos
from app.services.google_photos import GoogleAuthError, GoogleError, GoogleUnavailable

logger = logging.getLogger(__name__)
router = APIRouter(tags=["photo-imports"])

MAX_PICK = 50
# Used only if Google's session carries neither expireTime nor timeoutIn.
FALLBACK_SESSION_LIFETIME = timedelta(hours=1)


class PhotoImportCreated(BaseModel):
    id: uuid.UUID
    picker_uri: str
    status: str


class PhotoImportOut(BaseModel):
    id: uuid.UUID
    entry_id: uuid.UUID
    status: str
    added_count: int
    skipped_videos: int
    failed_names: list[str]


def _out(imp: PhotoImport) -> PhotoImportOut:
    return PhotoImportOut(
        id=imp.id,
        entry_id=imp.entry_id,
        status=imp.status,
        added_count=imp.added_count,
        skipped_videos=imp.skipped_videos,
        failed_names=list(imp.failed_names or []),
    )


async def _own_import(import_id: uuid.UUID, user: User, db: AsyncSession) -> PhotoImport:
    imp = (
        await db.execute(
            select(PhotoImport).where(PhotoImport.id == import_id, PhotoImport.user_id == user.id)
        )
    ).scalar_one_or_none()
    if imp is None:
        raise HTTPException(status_code=404, detail="not_found")
    return imp


async def _picker_access_token(user: User, db: AsyncSession) -> str:
    """The caller's fresh Google access token for the picker, or 409/503."""
    from app.workers.photo_imports import access_token_for

    try:
        return await access_token_for(db, user.id)
    except GoogleAuthError:
        raise HTTPException(status_code=409, detail="photos_not_connected") from None
    except GoogleUnavailable:
        raise HTTPException(status_code=503, detail="google_unavailable") from None


@router.post(
    "/entries/{entry_id}/photo-imports",
    status_code=http_status.HTTP_201_CREATED,
    response_model=PhotoImportCreated,
)
async def create_photo_import(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PhotoImportCreated:
    from app.routers.v1.entries import _get_entry_or_404
    from app.workers.photo_imports import poll_countdown
    from app.workers.tasks import process_photo_import

    await _get_entry_or_404(entry_id, user, db, require_editor=True)  # 403 for viewers
    token = await _picker_access_token(user, db)

    client = google_photos.GooglePhotosClient(token)
    try:
        session = await client.create_session(max_items=MAX_PICK)
    except GoogleAuthError:
        raise HTTPException(status_code=409, detail="photos_not_connected") from None
    except GoogleUnavailable:
        raise HTTPException(status_code=503, detail="google_unavailable") from None
    except GoogleError:
        raise HTTPException(status_code=502, detail="google_error") from None

    now = datetime.now(tz=UTC)
    if session.expire_time is not None:
        expires_at = session.expire_time
    elif session.timeout_s is not None:
        expires_at = now + timedelta(seconds=session.timeout_s)
    else:
        expires_at = now + FALLBACK_SESSION_LIFETIME

    imp = PhotoImport(
        user_id=user.id,
        entry_id=entry_id,
        google_session_id=session.id,
        status="waiting",
        expires_at=expires_at,
    )
    db.add(imp)
    await db.commit()

    try:
        process_photo_import.apply_async(
            args=(str(imp.id),),
            kwargs={"poll_s": session.poll_interval_s},
            countdown=poll_countdown(session.poll_interval_s),
        )
    except Exception:
        # Without the worker the import could never finish: say so now.
        logger.exception("photo_import_queue_failed import_id=%s", imp.id)
        imp.status = "failed"
        await db.commit()
        try:
            await client.delete_session(session.id)
        except Exception as exc:  # noqa: BLE001 — the session expires at Google anyway
            logger.info("photo_import_session_delete_failed error=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="queue_unavailable") from None

    logger.info("photo_import_started import_id=%s", imp.id)
    return PhotoImportCreated(id=imp.id, picker_uri=session.picker_uri, status=imp.status)


@router.get("/entries/{entry_id}/photo-imports", response_model=list[PhotoImportOut])
async def list_entry_photo_imports(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[PhotoImportOut]:
    """The caller's own imports into this entry that they haven't dismissed, newest first."""
    from app.routers.v1.entries import _get_entry_or_404

    await _get_entry_or_404(entry_id, user, db)
    rows = (
        await db.execute(
            select(PhotoImport)
            .where(
                PhotoImport.entry_id == entry_id,
                PhotoImport.user_id == user.id,
                PhotoImport.dismissed_at.is_(None),
            )
            .order_by(PhotoImport.created_at.desc(), PhotoImport.id.desc())
        )
    ).scalars()
    return [_out(imp) for imp in rows]


@router.get("/photo-imports/{import_id}", response_model=PhotoImportOut)
async def get_photo_import(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PhotoImportOut:
    return _out(await _own_import(import_id, user, db))


@router.post("/photo-imports/{import_id}/cancel", response_model=PhotoImportOut)
async def cancel_photo_import(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PhotoImportOut:
    """Stop waiting for (or copying) a pick. Photos already added stay; a finished import is unchanged.

    The worker deletes the Google session on its next run.
    """
    imp = await _own_import(import_id, user, db)
    # Guarded: a worker (or the sweep) that just ended the import keeps its status.
    await db.execute(
        update(PhotoImport)
        .where(PhotoImport.id == imp.id, PhotoImport.status.in_(("waiting", "copying")))
        .values(status="cancelled")
    )
    await db.commit()
    await db.refresh(imp)
    return _out(imp)


@router.post("/photo-imports/{import_id}/dismiss", status_code=http_status.HTTP_204_NO_CONTENT)
async def dismiss_photo_import(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    """Hide the import's outcome from the entry."""
    imp = await _own_import(import_id, user, db)
    if imp.dismissed_at is None:
        imp.dismissed_at = datetime.now(tz=UTC)
        await db.commit()
