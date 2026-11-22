"""The hand-over at the hard delete: chosen diaries and the photos that stay behind.

Called by hard_delete_user after the row-54 check and before the owned diaries are
deleted. These are retry-safe steps, not one transaction: each one re-checks state,
objects are copied before any row points at them, and the old copies are left for
the prefix scrub at the end of hard_delete_user. Any step may fail; the next hourly
run sees what was committed and finishes the rest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, select, update

from app.core.auth import deletion_state
from app.core.photo_crypto import unwrap_dek, wrap_dek
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
from app.services.diary_slugs import unique_slug
from app.services.sharing import lock_diary_sharing
from app.workers.utils import db_session

log = structlog.get_logger()

_PHOTO_KEY_ATTRS = ("s3_key", "thumbnail_s3_key", "display_s3_key")


# ---------------------------------------------------------------------------
# Diaries
# ---------------------------------------------------------------------------


async def transfer_chosen_diaries(departing_user_id: uuid.UUID) -> list[uuid.UUID]:
    """Give each diary the departing owner chose a recipient for to that recipient.

    One session per diary, so a failure leaves the diaries already moved moved.
    Returns the ids moved by this call.
    """
    async with db_session() as db:
        diary_ids = list(
            (
                await db.execute(
                    select(Diary.id).where(
                        Diary.owner_user_id == departing_user_id,
                        Diary.handover_to_user_id.is_not(None),
                    )
                )
            ).scalars()
        )
    moved: list[uuid.UUID] = []
    for diary_id in diary_ids:
        if await _transfer_one(diary_id, departing_user_id):
            moved.append(diary_id)
    return moved


async def _transfer_one(diary_id: uuid.UUID, departing_user_id: uuid.UUID) -> bool:
    async with db_session() as db:
        diary = (await db.execute(select(Diary).where(Diary.id == diary_id))).scalar_one_or_none()
        if diary is None:
            return False
        # Lock order, the same as the sharing endpoints: the diary's advisory share lock
        # (accept and revoke take it first), then the diary row (decline and remove_member
        # take it before touching a permission), then the recipient's permission row,
        # then the recipient's user row (FOR SHARE, so their own account delete waits).
        await lock_diary_sharing(db, diary)
        diary = (
            await db.execute(
                select(Diary)
                .where(Diary.id == diary_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if (
            diary is None
            or diary.owner_user_id != departing_user_id
            or diary.handover_to_user_id is None
        ):
            return False  # already handed over on an earlier run, or declined
        recipient_id = diary.handover_to_user_id

        reason: str | None = None
        if diary.deleted_at is not None:
            reason = "diary_deleted"
        perm = (
            await db.execute(
                select(DiaryPermission)
                .where(
                    DiaryPermission.diary_id == diary.id,
                    DiaryPermission.user_id == recipient_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if reason is None and perm is None:
            reason = "not_member"
        recipient = (
            await db.execute(
                select(User)
                .where(User.id == recipient_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if reason is None and (recipient is None or deletion_state(recipient) != "active"):
            reason = "recipient_not_active"
        if reason is not None:
            # The diary is then deleted with the rest of the account, as without a choice.
            diary.handover_to_user_id = None
            log.info("handover_skipped", diary_id=str(diary.id), reason=reason)
            return False
        assert perm is not None  # reason would be "not_member"

        # Before the owner changes: a query here autoflushes, and the new owner's
        # slugs are what must not clash (uq_diaries_owner_slug).
        slug = await unique_slug(db, recipient_id, diary.slug, exclude_diary_id=diary.id)
        diary.owner_user_id = recipient_id
        diary.owned_since = datetime.now(tz=UTC)
        diary.handover_to_user_id = None
        diary.slug = slug
        # Scanning used the old owner's Google account; the new owner turns it back on.
        diary.scan_enabled = False
        await db.delete(perm)
        await db.execute(
            delete(DiaryCalendarFilter).where(DiaryCalendarFilter.diary_id == diary.id)
        )
        # Events not in any entry came from the old owner's calendar.
        await db.execute(delete(Event).where(Event.diary_id == diary.id, Event.entry_id.is_(None)))
        await db.execute(
            update(ScanJob)
            .where(ScanJob.diary_id == diary.id)
            .values(
                last_calendar_cursor=None,
                last_photos_cursor=None,
                next_scan_after=None,
                consecutive_failures=0,
            )
        )
        # A backfill still waiting would read the new owner's Google account, which
        # they never pointed at this diary.
        await db.execute(
            update(BackfillRun)
            .where(BackfillRun.diary_id == diary.id, BackfillRun.status == "pending")
            .values(status="cancelled", completed_at=datetime.now(tz=UTC))
        )
        await db.execute(
            delete(Invitation).where(
                Invitation.diary_id == diary.id, Invitation.accepted_at.is_(None)
            )
        )
        db.add(
            AuditLog(
                action="diary.handover",
                target_type="diary",
                target_id=diary.id,
                meta={"to_user_id": str(recipient_id)},
            )
        )
    log.info("handover_diary_transferred", diary_id=str(diary_id), to_user_id=str(recipient_id))
    return True


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


async def move_departing_photos(departing_user_id: uuid.UUID) -> int:
    """Move the departing user's photos that live on in other people's diaries.

    Covers every finalized, live photo of theirs attached to an entry (soft-deleted
    entries and diaries included) in a diary they don't own. Each goes to the owner of
    the diary holding its earliest attachment, by Entry.created_at, Entry.id. Run after
    transfer_chosen_diaries, so a handed-over diary already counts as not theirs.
    Returns how many photos were processed.
    """
    async with db_session() as db:
        rows = (
            await db.execute(
                select(Photo.id, Diary.owner_user_id)
                .join(EntryPhoto, EntryPhoto.photo_id == Photo.id)
                .join(Entry, Entry.id == EntryPhoto.entry_id)
                .join(Diary, Diary.id == Entry.diary_id)
                .where(
                    Photo.user_id == departing_user_id,
                    Photo.finalized_at.is_not(None),
                    Photo.deleted_at.is_(None),
                    Photo.dek_ciphertext.is_not(None),
                    Diary.owner_user_id != departing_user_id,
                )
                .order_by(Photo.id, Entry.created_at, Entry.id)
                .distinct(Photo.id)
            )
        ).all()
    for photo_id, new_owner_id in rows:
        await _move_photo(photo_id, departing_user_id, new_owner_id)
    return len(rows)


def _moved_key(key: str, departing_user_id: uuid.UUID, new_owner_id: uuid.UUID) -> str:
    prefix = f"{departing_user_id}/"
    if key.startswith(prefix):
        return f"{new_owner_id}/{key[len(prefix) :]}"
    return f"{new_owner_id}/{key.rsplit('/', 1)[-1]}"


def _is_missing_source(exc: Exception) -> bool:
    from botocore.exceptions import ClientError

    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    return error.get("Code") in ("NoSuchKey", "404")


async def _move_photo(
    photo_id: uuid.UUID, departing_user_id: uuid.UUID, new_owner_id: uuid.UUID
) -> None:
    """Make *new_owner_id* the photo's owner. Safe to run again after any failure."""
    from app.core.config import get_settings
    from app.core.dependencies import get_s3

    async with db_session() as db:
        photo = (
            await db.execute(
                select(Photo)
                .where(Photo.id == photo_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if photo is None or photo.user_id != departing_user_id:
            return  # moved (or merged) on an earlier run
        if photo.dek_ciphertext is None:
            return

        if photo.source == "google_photos" and photo.external_id is not None:
            existing_id = (
                await db.execute(
                    select(Photo.id).where(
                        Photo.user_id == new_owner_id,
                        Photo.source == photo.source,
                        Photo.external_id == photo.external_id,
                        Photo.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing_id is not None:
                # The new owner already has this Google photo: keep theirs, point every
                # link at it, and drop this row. Its objects stay for the prefix scrub.
                already_linked = select(EntryPhoto.entry_id).where(
                    EntryPhoto.photo_id == existing_id
                )
                await db.execute(
                    delete(EntryPhoto)
                    .where(
                        EntryPhoto.photo_id == photo.id,
                        EntryPhoto.entry_id.in_(already_linked),
                    )
                    .execution_options(synchronize_session=False)
                )
                await db.execute(
                    update(EntryPhoto)
                    .where(EntryPhoto.photo_id == photo.id)
                    .values(photo_id=existing_id)
                    .execution_options(synchronize_session=False)
                )
                await db.execute(delete(Photo).where(Photo.id == photo.id))
                log.info(
                    "handover_photo_moved", photo_id=str(photo_id), to_user_id=str(new_owner_id)
                )
                return

        # Copy first; a row only ever points at objects that exist. A copy left behind
        # by a run that failed before the commit is overwritten by the next one.
        settings = get_settings()
        bucket = settings.s3_bucket_photos
        s3 = get_s3()
        new_keys: dict[str, str | None] = {}
        for attr in _PHOTO_KEY_ATTRS:
            old_key = getattr(photo, attr)
            if not old_key:
                continue
            new_key = _moved_key(old_key, departing_user_id, new_owner_id)
            try:
                s3.copy_object(
                    Bucket=bucket, Key=new_key, CopySource={"Bucket": bucket, "Key": old_key}
                )
            except Exception as e:
                if _is_missing_source(e):
                    # Gone already (not a storage outage): retrying would never finish
                    # the account deletion. A missing original means the photo is
                    # unreadable, so it is not moved and goes with the account; a
                    # missing thumbnail or display copy is dropped from the row.
                    log.warning("handover_photo_source_missing", photo_id=str(photo_id), kind=attr)
                    if attr == "s3_key":
                        return
                    new_keys[attr] = None
                    continue
                log.warning(
                    "handover_photo_move_failed", photo_id=str(photo_id), error=type(e).__name__
                )
                raise
            new_keys[attr] = new_key

        photo.dek_ciphertext = wrap_dek(
            unwrap_dek(photo.dek_ciphertext, departing_user_id), new_owner_id
        )
        for attr, key in new_keys.items():
            setattr(photo, attr, key)
        photo.user_id = new_owner_id
    log.info("handover_photo_moved", photo_id=str(photo_id), to_user_id=str(new_owner_id))
