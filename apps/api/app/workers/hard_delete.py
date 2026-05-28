"""Hard delete cascade logic for diaries and users."""

from __future__ import annotations

import uuid

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
        DiaryPhoto,
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

            # Check which photos exist in other diaries (via diary_photos)
            for photo_id in list(photo_ids_in_diary):
                other_result = await db.execute(
                    select(DiaryPhoto)
                    .where(
                        DiaryPhoto.photo_id == photo_id,
                        DiaryPhoto.diary_id != diary_id,
                    )
                    .limit(1)
                )
                if other_result.scalar_one_or_none() is None:
                    # Safe to delete
                    try:
                        settings = get_settings()
                        photo_result = await db.execute(select(Photo).where(Photo.id == photo_id))
                        photo = photo_result.scalar_one_or_none()
                        if photo:
                            s3 = get_s3()
                            for key in [photo.s3_key, photo.thumbnail_s3_key]:
                                if key:
                                    try:
                                        s3.delete_object(Bucket=settings.s3_bucket_photos, Key=key)
                                    except Exception:
                                        pass
                    except Exception as e:
                        log.warning(
                            "hard_delete_photo_s3_error", photo_id=str(photo_id), error=str(e)
                        )

                    await db.execute(delete(Photo).where(Photo.id == photo_id))

        # Cascade delete in reverse FK order
        if entry_ids:
            await db.execute(delete(EntryEditDiff).where(EntryEditDiff.entry_id.in_(entry_ids)))
            await db.execute(delete(LLMGeneration).where(LLMGeneration.entry_id.in_(entry_ids)))
            await db.execute(delete(Enrichment).where(Enrichment.entry_id.in_(entry_ids)))
            await db.execute(delete(EntryPhoto).where(EntryPhoto.entry_id.in_(entry_ids)))
            await db.execute(delete(Event).where(Event.entry_id.in_(entry_ids)))
            await db.execute(delete(Entry).where(Entry.diary_id == diary_id))

        await db.execute(delete(DiaryPhoto).where(DiaryPhoto.diary_id == diary_id))
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

    log.info("hard_delete_user_start", user_id=str(user_id))

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
        await db.execute(delete(OAuthToken).where(OAuthToken.user_id == user_id))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await db.execute(
            delete(MagicLinkToken).where(
                # Can't FK to user_id on magic_link_tokens but clean up by matching email
            )
        )
        await db.execute(delete(SocialIdentity).where(SocialIdentity.user_id == user_id))

        # Scrub MinIO: delete everything under {user_id}/ prefix and tmp/{user_id}/ prefix
        try:
            settings = get_settings()
            s3 = get_s3()
            paginator = s3.get_paginator("list_objects_v2")
            for prefix in (f"{user_id}/", f"tmp/{user_id}/"):
                for page in paginator.paginate(Bucket=settings.s3_bucket_photos, Prefix=prefix):
                    objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                    if objects:
                        s3.delete_objects(Bucket=settings.s3_bucket_photos, Delete={"Objects": objects})
        except Exception as e:
            log.warning("hard_delete_user_s3_error", user_id=str(user_id), error=str(e))

        # Anonymize audit_log (null user_id, keep action/timestamp)
        await db.execute(update(AuditLog).where(AuditLog.user_id == user_id).values(user_id=None))

        await db.execute(delete(User).where(User.id == user_id))

    log.info("hard_delete_user_done", user_id=str(user_id))
