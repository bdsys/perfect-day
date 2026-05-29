"""Beat tasks: periodic dispatch jobs."""

from __future__ import annotations

import structlog

from app.workers.celery_app import celery_app
from app.workers.utils import run_sync

log = structlog.get_logger()


@celery_app.task(name="app.workers.beat_tasks.dispatch_due_scans")
def dispatch_due_scans() -> None:
    run_sync(_dispatch_due_scans())


async def _dispatch_due_scans() -> None:

    from sqlalchemy import func, select

    from app.models import Diary, ScanJob
    from app.workers.tasks import scan_diary
    from app.workers.utils import db_session

    async with db_session() as db:
        now = func.now()
        result = await db.execute(
            select(Diary.id)
            .join(ScanJob, ScanJob.diary_id == Diary.id)
            .where(
                Diary.deleted_at.is_(None),
                Diary.scan_enabled.is_(True),
                (ScanJob.next_scan_after.is_(None)) | (ScanJob.next_scan_after <= now),
            )
        )
        diary_ids = [str(r[0]) for r in result.fetchall()]

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

    for diary_id in diary_ids:
        await hard_delete_diary(diary_id)

    for user_id in user_ids:
        await hard_delete_user(user_id)


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
    return deleted
