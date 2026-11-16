"""Beat tasks: periodic dispatch jobs."""

from __future__ import annotations

import uuid

import structlog

from app.workers.celery_app import celery_app
from app.workers.utils import run_sync

log = structlog.get_logger()


@celery_app.task(name="app.workers.beat_tasks.dispatch_due_scans")
def dispatch_due_scans() -> None:
    run_sync(_dispatch_due_scans())


async def _dispatch_due_scans() -> None:

    from sqlalchemy import func, select

    from app.models import Diary, ScanJob, User
    from app.services.read_only import read_only_diary_ids
    from app.workers.tasks import scan_diary
    from app.workers.utils import db_session

    async with db_session() as db:
        now = func.now()
        result = await db.execute(
            select(Diary.id, Diary.owner_user_id, User.subscription_tier)
            .join(ScanJob, ScanJob.diary_id == Diary.id)
            .join(User, User.id == Diary.owner_user_id)
            .where(
                Diary.deleted_at.is_(None),
                # An account in its deletion grace window must not keep being scanned.
                User.deleted_at.is_(None),
                Diary.scan_enabled.is_(True),
                (ScanJob.next_scan_after.is_(None)) | (ScanJob.next_scan_after <= now),
            )
        )
        due = result.fetchall()
        # One lookup per owner, not per diary.
        read_only: dict[uuid.UUID, set[uuid.UUID]] = {}
        diary_ids = []
        for diary_id, owner_id, tier in due:
            if owner_id not in read_only:
                read_only[owner_id] = await read_only_diary_ids(db, owner_id, tier)
            if diary_id in read_only[owner_id]:
                log.info("diary_read_only_skip", diary_id=str(diary_id), where="beat")
                continue
            diary_ids.append(str(diary_id))

    log.info("dispatch_due_scans", count=len(diary_ids))
    for diary_id in diary_ids:
        scan_diary.delay(diary_id)


@celery_app.task(name="app.workers.beat_tasks.process_hard_deletes")
def process_hard_deletes() -> None:
    run_sync(_process_hard_deletes())


async def _process_hard_deletes() -> None:

    from sqlalchemy import func, select

    from app.models import Diary, User
    from app.workers.hard_delete import hard_delete_diary, hard_delete_user
    from app.workers.utils import db_session

    async with db_session() as db:
        now = func.now()

        # Diaries due for hard delete
        diary_result = await db.execute(
            select(Diary.id).where(
                Diary.hard_delete_after.is_not(None),
                Diary.hard_delete_after <= now,
            )
        )
        diary_ids = [r[0] for r in diary_result.fetchall()]

        # Users due for hard delete
        user_result = await db.execute(
            select(User.id).where(
                User.hard_delete_after.is_not(None),
                User.hard_delete_after <= now,
            )
        )
        user_ids = [r[0] for r in user_result.fetchall()]

    log.info("process_hard_deletes", diaries=len(diary_ids), users=len(user_ids))

    # One failing row must not block every deletion queued behind it, on this
    # run and on every later one.
    for diary_id in diary_ids:
        try:
            await hard_delete_diary(diary_id)
        except Exception:
            log.exception("hard_delete_diary_failed", diary_id=str(diary_id))

    for user_id in user_ids:
        try:
            await hard_delete_user(user_id)
        except Exception:
            log.exception("hard_delete_user_failed", user_id=str(user_id))


@celery_app.task(name="app.workers.beat_tasks.sweep_stuck_photo_imports")
def sweep_stuck_photo_imports() -> None:
    from app.workers.photo_imports import sweep_stuck_imports

    run_sync(sweep_stuck_imports())


@celery_app.task(name="app.workers.beat_tasks.release_lost_ai_drafts")
def release_lost_ai_drafts() -> int:
    return run_sync(_release_lost_ai_drafts())


async def _release_lost_ai_drafts() -> int:
    from app.services.ai_drafts import release_lost_ai_drafts as sweep
    from app.workers.utils import db_session

    async with db_session() as db:
        return await sweep(db)


@celery_app.task(name="app.workers.beat_tasks.sweep_orphaned_photos")
def sweep_orphaned_photos() -> None:
    run_sync(_sweep_orphaned_photos())


async def _sweep_orphaned_photos() -> int:
    """Delete unfinalized Photo rows older than 24h and stray tmp/ objects."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.dependencies import get_s3
    from app.models import Photo
    from app.workers.utils import db_session

    settings = get_settings()
    s3 = get_s3()
    cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
    bucket = settings.s3_bucket_photos

    deleted = 0
    async with db_session() as db:
        result = await db.execute(
            select(Photo).where(
                Photo.finalized_at.is_(None),
                Photo.created_at < cutoff,
            )
        )
        for photo in result.scalars().all():
            # Best-effort delete of any tmp object for this photo
            tmp_key = f"tmp/{photo.user_id}/{photo.id}"
            try:
                s3.delete_object(Bucket=bucket, Key=tmp_key)
            except Exception:  # noqa: BLE001
                pass
            await db.delete(photo)
            deleted += 1

    # Reconcile tmp/ prefix: anything older than 24h with no row gets deleted
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix="tmp/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"].replace(tzinfo=UTC) < cutoff:
                    try:
                        s3.delete_object(Bucket=bucket, Key=obj["Key"])
                        deleted += 1
                    except Exception:  # noqa: BLE001
                        pass
    except Exception:  # noqa: BLE001
        pass

    log.info("sweep_orphaned_photos", deleted=deleted)
    await create_missing_display_copies()
    return deleted


async def create_missing_display_copies(limit: int = 20) -> int:
    """Give HEIC/HEIF photos that lack a JPEG display copy one. Returns how many were made."""
    import asyncio

    from sqlalchemy import func, select

    from app.core.photo_crypto import PURPOSE_DISPLAY, decrypt_stream, encrypt_stream, unwrap_dek
    from app.models import Photo
    from app.services.photos import get_object_bytes, make_display_jpeg, put_object_bytes
    from app.workers.utils import db_session

    loop = asyncio.get_running_loop()
    made = 0
    async with db_session() as db:
        photos = (
            await db.execute(
                select(Photo)
                .where(
                    Photo.mime_type.in_(["image/heic", "image/heif"]),
                    Photo.display_s3_key.is_(None),
                    Photo.finalized_at.is_not(None),
                    Photo.deleted_at.is_(None),
                    Photo.dek_ciphertext.is_not(None),
                )
                .order_by(Photo.display_attempted_at.asc().nulls_first(), Photo.created_at)
                .limit(limit)
            )
        ).scalars().all()
        for photo in photos:
            # Stamped on every attempt so a failing photo goes behind untried ones.
            photo.display_attempted_at = func.now()
            try:
                dek = unwrap_dek(photo.dek_ciphertext, photo.user_id)
                key = f"{photo.user_id}/{photo.id}_display.enc"

                def _build(photo=photo, dek=dek, key=key) -> bool:
                    original = decrypt_stream(get_object_bytes(photo.s3_key), dek)
                    jpeg = make_display_jpeg(original, photo.mime_type)
                    if jpeg is None:
                        return False
                    put_object_bytes(key, encrypt_stream(jpeg, dek, PURPOSE_DISPLAY))
                    return True

                if await loop.run_in_executor(None, _build):
                    photo.display_s3_key = key
                    made += 1
            except Exception:  # noqa: BLE001 — one bad photo must not stop the rest
                log.warning("display_copy_failed", photo_id=str(photo.id))
    log.info("create_missing_display_copies", made=made)
    return made
