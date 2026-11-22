"""Hard delete cascade logic for diaries and users."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.workers.utils import db_session

log = structlog.get_logger()


async def hard_delete_diary(diary_id: uuid.UUID) -> None:
    from sqlalchemy import delete, select

    from app.core.config import get_settings
    from app.core.dependencies import get_s3
    from app.models import (
        AuditLog,
        BackfillRun,
        Diary,
        DiaryCalendarFilter,
        DiaryPermission,
        Enrichment,
        Entry,
        EntryEditDiff,
        EntryPhoto,
        Event,
        Invitation,
        LLMGeneration,
        Photo,
        ScanJob,
        ScanRun,
    )

    log.info("hard_delete_diary_start", diary_id=str(diary_id))

    async with db_session() as db:
        # Get all entry IDs
        entry_result = await db.execute(select(Entry.id).where(Entry.diary_id == diary_id))
        entry_ids = [r[0] for r in entry_result.fetchall()]

        # Get photos that are exclusively linked to this diary
        if entry_ids:
            # Photos attached to entries in this diary
            entry_photo_result = await db.execute(
                select(EntryPhoto.photo_id).where(EntryPhoto.entry_id.in_(entry_ids))
            )
            photo_ids_in_diary = {r[0] for r in entry_photo_result.fetchall()}

            # Photos live in a user-wide library and can be attached to entries in
            # several diaries. Only delete the ones no other diary still uses;
            # the rest just lose their entry_photos rows via the entry cascade.
            if photo_ids_in_diary:
                shared_result = await db.execute(
                    select(EntryPhoto.photo_id)
                    .join(Entry, Entry.id == EntryPhoto.entry_id)
                    .where(
                        EntryPhoto.photo_id.in_(photo_ids_in_diary),
                        Entry.diary_id != diary_id,
                    )
                )
                photo_ids_in_diary -= {r[0] for r in shared_result.fetchall()}

            # A storage error stops the whole delete: the transaction rolls back, the
            # diary stays due, and the hourly job tries again. Deleting the rows
            # anyway would leave the objects in storage with nothing to find them.
            settings = get_settings()
            for photo_id in list(photo_ids_in_diary):
                photo_result = await db.execute(select(Photo).where(Photo.id == photo_id))
                photo = photo_result.scalar_one_or_none()
                if photo:
                    s3 = get_s3()
                    for key in [photo.s3_key, photo.thumbnail_s3_key, photo.display_s3_key]:
                        if key:
                            try:
                                s3.delete_object(Bucket=settings.s3_bucket_photos, Key=key)
                            except Exception as e:
                                log.warning(
                                    "hard_delete_diary_storage_failed",
                                    diary_id=str(diary_id),
                                    photo_id=str(photo_id),
                                    error=type(e).__name__,
                                )
                                raise

                await db.execute(delete(Photo).where(Photo.id == photo_id))

        # Cascade delete in reverse FK order
        if entry_ids:
            await db.execute(delete(EntryEditDiff).where(EntryEditDiff.entry_id.in_(entry_ids)))
            await db.execute(delete(LLMGeneration).where(LLMGeneration.entry_id.in_(entry_ids)))
            await db.execute(delete(Enrichment).where(Enrichment.entry_id.in_(entry_ids)))
            await db.execute(delete(EntryPhoto).where(EntryPhoto.entry_id.in_(entry_ids)))
            await db.execute(delete(Event).where(Event.entry_id.in_(entry_ids)))
            await db.execute(delete(Entry).where(Entry.diary_id == diary_id))

        await db.execute(delete(ScanRun).where(ScanRun.diary_id == diary_id))
        await db.execute(delete(BackfillRun).where(BackfillRun.diary_id == diary_id))
        await db.execute(
            delete(DiaryCalendarFilter).where(DiaryCalendarFilter.diary_id == diary_id)
        )
        await db.execute(delete(DiaryPermission).where(DiaryPermission.diary_id == diary_id))
        await db.execute(delete(Invitation).where(Invitation.diary_id == diary_id))
        await db.execute(delete(ScanJob).where(ScanJob.diary_id == diary_id))
        await db.execute(delete(Diary).where(Diary.id == diary_id))

        db.add(
            AuditLog(
                action="diary.hard_delete",
                target_type="diary",
                target_id=diary_id,
            )
        )

    log.info("hard_delete_diary_done", diary_id=str(diary_id))


async def hard_delete_user(user_id: uuid.UUID) -> None:
    from sqlalchemy import delete, select, update

    from app.core.config import get_settings
    from app.core.dependencies import get_s3
    from app.models import (
        AuditLog,
        Diary,
        MagicLinkToken,
        Notification,
        NotificationPreferences,
        OAuthToken,
        RefreshToken,
        SocialIdentity,
        User,
    )
    from app.services.google_revoke import revoke_and_forget_google

    log.info("hard_delete_user_start", user_id=str(user_id))

    # Row 54: lock the account and check it is still due. A restore that landed after
    # the hourly job picked this account has cleared both columns; restore takes the
    # same lock and refuses once the deadline has passed, so the two never cross.
    # No row at all is a retry after a partial run: carry on and finish the cleanup.
    async with db_session() as db:
        # key_share=True renders FOR NO KEY UPDATE: it orders the four paths that lock
        # this row (restore, account delete, the Google callback, this) without
        # blocking the KEY SHARE lock foreign-key inserts take, so a refresh-token
        # rotation running at the same time cannot deadlock against it.
        account = (
            await db.execute(
                select(User).where(User.id == user_id).with_for_update(key_share=True)
            )
        ).scalar_one_or_none()
        if account is not None and not (
            account.deleted_at is not None
            and account.hard_delete_after is not None
            and account.hard_delete_after <= datetime.now(tz=UTC)
        ):
            log.info("hard_delete_user_skipped_not_due", user_id=str(user_id))
            return

    # The hand-over (design: diary sharing). First the diaries the owner chose a
    # recipient for change hands; then their photos still attached in diaries they
    # don't own (those just handed over included) move to those diaries' owners. Both
    # re-check state and only copy objects, so a failure is finished by the next run;
    # the old copies go with the prefix scrub below.
    from app.workers.handover import move_departing_photos, transfer_chosen_diaries

    await transfer_chosen_diaries(user_id)
    await move_departing_photos(user_id)

    async with db_session() as db:
        # Hard delete all owned diaries first
        diary_result = await db.execute(select(Diary.id).where(Diary.owner_user_id == user_id))
        diary_ids = [r[0] for r in diary_result.fetchall()]

    for diary_id in diary_ids:
        await hard_delete_diary(diary_id)

    async with db_session() as db:
        # Delete user-level data
        await db.execute(delete(Notification).where(Notification.user_id == user_id))
        await db.execute(
            delete(NotificationPreferences).where(NotificationPreferences.user_id == user_id)
        )
        # Revoke any Google grant still held (an admin delete never revoked it, and a
        # connect may have finished after the account was deleted), then forget it.
        google_tokens = (
            (
                await db.execute(
                    select(OAuthToken).where(
                        OAuthToken.user_id == user_id, OAuthToken.provider == "google"
                    )
                )
            )
            .scalars()
            .all()
        )
        for token in google_tokens:
            await revoke_and_forget_google(db, token, user_id=user_id)
        await db.execute(delete(OAuthToken).where(OAuthToken.user_id == user_id))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        # magic_link_tokens has no user FK; match on the user's email. An empty
        # .where() here would delete every user's tokens.
        email_result = await db.execute(select(User.email).where(User.id == user_id))
        user_email = email_result.scalar_one_or_none()
        if user_email:
            await db.execute(delete(MagicLinkToken).where(MagicLinkToken.email == user_email))
        await db.execute(delete(SocialIdentity).where(SocialIdentity.user_id == user_id))

        # Scrub the photo store: everything under {user_id}/ and tmp/{user_id}/.
        # A storage error stops the delete (the user row stays, so the hourly job
        # retries); deleting the row anyway would leave the photos in storage for good.
        try:
            settings = get_settings()
            s3 = get_s3()
            paginator = s3.get_paginator("list_objects_v2")
            for prefix in (f"{user_id}/", f"tmp/{user_id}/"):
                for page in paginator.paginate(Bucket=settings.s3_bucket_photos, Prefix=prefix):
                    objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                    if objects:
                        result = s3.delete_objects(
                            Bucket=settings.s3_bucket_photos, Delete={"Objects": objects}
                        )
                        if result.get("Errors"):
                            raise RuntimeError(
                                f"storage refused {len(result['Errors'])} object deletes"
                            )
        except Exception as e:
            log.warning(
                "hard_delete_user_storage_failed", user_id=str(user_id), error=type(e).__name__
            )
            raise

        # Anonymize audit_log (null user_id, keep action/timestamp)
        await db.execute(update(AuditLog).where(AuditLog.user_id == user_id).values(user_id=None))

        await db.execute(delete(User).where(User.id == user_id))

    log.info("hard_delete_user_done", user_id=str(user_id))
