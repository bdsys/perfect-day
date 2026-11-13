"""Copy a parent's Google Photos picks into their library and onto an entry (item 14).

Runs inside the ``process_photo_import`` Celery task, so it finishes even when
the parent's phone tab has closed. One run either polls the picker session
once (and asks to be run again after Google's poll interval) or copies every
picked item, one at a time, committing after each.

Privacy: logs carry the import id and counts only, never tokens, base URLs,
file names or bytes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import structlog
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EntryPhoto, OAuthToken, Photo, PhotoImport, User
from app.services import google_photos
from app.services.google_photos import (
    GoogleAuthError,
    GoogleError,
    GoogleUnavailable,
    PickedItem,
)

log = structlog.get_logger()

MIN_POLL_S = 1.0
MAX_POLL_S = 60.0
LIST_RETRIES = 3  # Ruling P1: retries after the first try, then "failed"
ACTIVE = ("waiting", "copying")


@dataclass(frozen=True)
class Requeue:
    """Run the import again after *countdown* seconds."""

    countdown: float
    list_attempt: int = 0
    poll_s: float | None = None  # Google's poll interval, carried between runs


@dataclass(frozen=True)
class _CopyNow:
    """This run claimed the finished pick ("copying") and must copy it now."""

    poll_s: float | None


def poll_countdown(seconds: float | None) -> float:
    """Google's poll interval, kept within sane bounds."""
    if seconds is None:
        return google_photos.DEFAULT_POLL_S
    return min(max(seconds, MIN_POLL_S), MAX_POLL_S)


async def access_token_for(db: AsyncSession, user_id: uuid.UUID) -> str:
    """A fresh access token for the user's Google Photos connection.

    Raises GoogleAuthError when the parent must reconnect (no connection, no
    picker scope, revoked, refresh refused for good) and GoogleUnavailable when
    a refresh failed for a reason that may pass.
    """
    from app.routers.v1.integrations import PHOTOS_SHORT
    from app.workers.token_refresh import ensure_fresh_access_token

    row = (
        await db.execute(
            select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.provider == "google")
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or PHOTOS_SHORT not in (row.scopes_granted or []):
        raise GoogleAuthError("photos not connected")
    token = await ensure_fresh_access_token(row, db)
    if token:
        return token
    await db.commit()  # keep a revoked_at set by an invalid_grant refresh
    if row.revoked_at is not None or not row.refresh_token_ciphertext:
        raise GoogleAuthError("token refresh refused")
    raise GoogleUnavailable("token refresh failed")


async def delete_session_quietly(db: AsyncSession, imp: PhotoImport) -> None:
    """Best effort: the session expires at Google anyway."""
    try:
        token = await access_token_for(db, imp.user_id)
        await google_photos.GooglePhotosClient(token).delete_session(imp.google_session_id)
    except Exception as exc:  # noqa: BLE001 — never fail an import over clean-up
        log.info("photo_import_session_delete_failed", import_id=str(imp.id), error=type(exc).__name__)


async def _load(db: AsyncSession, import_id: uuid.UUID) -> PhotoImport | None:
    return (
        await db.execute(
            select(PhotoImport)
            .where(PhotoImport.id == import_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _set_status(db: AsyncSession, imp: PhotoImport, status: str) -> None:
    imp.status = status
    await db.commit()
    log.info(
        "photo_import_finished",
        import_id=str(imp.id),
        status=status,
        added=imp.added_count,
        skipped_videos=imp.skipped_videos,
        failed=len(imp.failed_names or []),
    )


async def mark_failed(import_id: uuid.UUID) -> None:
    """Used when the import can't be queued again: it would otherwise wait forever."""
    from app.workers.utils import db_session

    async with db_session() as db:
        imp = await _load(db, import_id)
        if imp is not None and imp.status in ACTIVE:
            await _set_status(db, imp, "failed")


async def run_photo_import(
    import_id: uuid.UUID, *, list_attempt: int = 0, poll_s: float | None = None
) -> Requeue | None:
    """Do one step of the import. Returns a Requeue when it must run again.

    *poll_s* is the poll interval Google gave last; outage retries wait that long.
    """
    from app.workers.utils import db_session

    async with db_session() as db:
        imp = await _load(db, import_id)
        if imp is None:
            return None
        if imp.status == "cancelled":
            await delete_session_quietly(db, imp)
            return None
        if imp.status not in ACTIVE:
            return None
        try:
            if imp.status == "waiting":
                step = await _poll(db, imp, poll_s)
                if not isinstance(step, _CopyNow):
                    return step
                poll_s = step.poll_s
            return await _copy(db, imp, list_attempt, poll_s)
        except GoogleAuthError:
            await db.rollback()
            imp = await _load(db, import_id)
            if imp is not None and imp.status in ACTIVE:
                await _set_status(db, imp, "needs_reconnect")
            return None


async def _poll(
    db: AsyncSession, imp: PhotoImport, poll_s: float | None
) -> Requeue | _CopyNow | None:
    """Check the picker session once; None when the import has ended."""
    if imp.expires_at is not None and datetime.now(tz=UTC) >= imp.expires_at:
        await _set_status(db, imp, "expired")
        await delete_session_quietly(db, imp)
        return None
    try:
        token = await access_token_for(db, imp.user_id)
        session = await google_photos.GooglePhotosClient(token).get_session(imp.google_session_id)
    except GoogleUnavailable:
        # Ruling P1: try again at the next poll, until expires_at.
        log.info("photo_import_poll_unavailable", import_id=str(imp.id))
        return Requeue(poll_countdown(poll_s), poll_s=poll_s)
    except GoogleAuthError:
        raise
    except GoogleError:
        await _set_status(db, imp, "failed")
        return None
    if not session.items_set:
        return Requeue(poll_countdown(session.poll_interval_s), poll_s=session.poll_interval_s)

    # Claim the copy; a cancel (or another run) that got there first wins.
    claimed = await db.execute(
        update(PhotoImport)
        .where(PhotoImport.id == imp.id, PhotoImport.status == "waiting")
        .values(status="copying")
    )
    await db.commit()
    await db.refresh(imp)
    if cast(CursorResult, claimed).rowcount == 1:
        return _CopyNow(session.poll_interval_s)
    if imp.status == "cancelled":
        await delete_session_quietly(db, imp)
    return None


async def _copy(
    db: AsyncSession, imp: PhotoImport, list_attempt: int, poll_s: float | None
) -> Requeue | None:
    from fastapi import HTTPException

    from app.routers.v1.entries import _get_entry_or_404
    from app.services.photo_ingest import ingest_photo
    from app.workers.tasks import queue_location_refresh

    entry_id = imp.entry_id
    user = await db.get(User, imp.user_id)
    if user is None:
        await _set_status(db, imp, "failed")
        await delete_session_quietly(db, imp)
        return None
    try:
        # The picker may have been open for a while: the parent must still edit the entry.
        await _get_entry_or_404(entry_id, user, db, require_editor=True)
    except HTTPException:
        await _set_status(db, imp, "failed")
        await delete_session_quietly(db, imp)
        return None

    try:
        token = await access_token_for(db, imp.user_id)
        items = await google_photos.GooglePhotosClient(token).list_items(imp.google_session_id)
    except GoogleUnavailable:
        if list_attempt < LIST_RETRIES:
            log.info("photo_import_list_unavailable", import_id=str(imp.id), attempt=list_attempt)
            return Requeue(poll_countdown(poll_s), list_attempt + 1, poll_s)
        await _set_status(db, imp, "failed")
        return None
    except GoogleAuthError:
        raise
    except GoogleError:
        await _set_status(db, imp, "failed")
        return None

    # A pass copies every item; a re-delivered task starts the pass again, so
    # per-pass counts restart. Items already attached are not counted twice.
    imp.skipped_videos = 0
    imp.failed_names = []
    await db.commit()

    added_any = False
    try:
        for item in items:
            await db.refresh(imp, ["status"])
            if imp.status != "copying":  # cancelled meanwhile
                break
            if item.type == "VIDEO":
                imp.skipped_videos += 1
                await db.commit()
                continue
            try:
                photo = await _library_photo(db, imp.user_id, item.id)
                if photo is None:
                    token = await access_token_for(db, imp.user_id)
                    data = await google_photos.GooglePhotosClient(token).download(item)
                    photo = await ingest_photo(
                        db,
                        owner=user,
                        data=data,
                        source="google_photos",
                        external_id=item.id,
                        taken_at_fallback=item.create_time,
                    )
                    del data
                if await _attach(db, entry_id, photo.id):
                    imp.added_count += 1
                    added_any = True
                await db.commit()
            except GoogleAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad item must not stop the rest
                await db.rollback()  # expires everything loaded: reload what the loop uses
                await db.refresh(imp)
                await db.refresh(user)
                imp.failed_names = [*(imp.failed_names or []), _name(item)]
                await db.commit()
                log.warning(
                    "photo_import_item_failed", import_id=str(imp.id), error=type(exc).__name__
                )
        else:
            await delete_session_quietly(db, imp)
            await _set_status(db, imp, "done")
        if imp.status == "cancelled":
            await delete_session_quietly(db, imp)
    finally:
        if added_any:
            queue_location_refresh(entry_id)
    return None


def _name(item: PickedItem) -> str:
    return item.filename or item.id


async def _library_photo(db: AsyncSession, user_id: uuid.UUID, item_id: str) -> Photo | None:
    """The user's own earlier copy of this Google item, if they still have it."""
    return (
        await db.execute(
            select(Photo).where(
                Photo.user_id == user_id,
                Photo.source == "google_photos",
                Photo.external_id == item_id,
                Photo.deleted_at.is_(None),
                Photo.finalized_at.is_not(None),
            )
        )
    ).scalar_one_or_none()


async def _attach(db: AsyncSession, entry_id: uuid.UUID, photo_id: uuid.UUID) -> bool:
    """Link the photo to the entry; False if it was already there."""
    existing = (
        await db.execute(
            select(EntryPhoto).where(EntryPhoto.entry_id == entry_id, EntryPhoto.photo_id == photo_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    db.add(EntryPhoto(entry_id=entry_id, photo_id=photo_id))
    await db.flush()
    return True
