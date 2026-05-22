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


async def _sweep_orphaned_photos() -> None:

    from sqlalchemy import func, select

    from app.models import Photo
    from app.workers.utils import db_session

    async with db_session() as db:
        cutoff = func.now() - func.cast(
            "24 hours", __import__("sqlalchemy", fromlist=["text"]).text("interval")
        )
        result = await db.execute(
            select(Photo).where(
                Photo.finalized_at.is_(None),
                Photo.created_at < cutoff,
                Photo.deleted_at.is_(None),
            )
        )
        photos = result.scalars().all()

    log.info("sweep_orphaned_photos", count=len(photos))
    for photo in photos:
        try:
            from app.core.config import get_settings
            from app.core.dependencies import get_s3

            settings = get_settings()
            get_s3().delete_object(Bucket=settings.s3_bucket_photos, Key=photo.s3_key)
        except Exception as e:
            log.warning("sweep_orphaned_photo_s3_error", photo_id=str(photo.id), error=str(e))

        async with db_session() as db2:
            import datetime

            from sqlalchemy import select as sel

            from app.models import Photo as P

            r = await db2.execute(sel(P).where(P.id == photo.id))
            p = r.scalar_one_or_none()
            if p:
                p.deleted_at = datetime.datetime.now(tz=datetime.UTC)
