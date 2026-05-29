from __future__ import annotations

from datetime import timedelta

from celery import Celery

from app.core.config import get_settings


def create_celery() -> Celery:
    settings = get_settings()

    app = Celery(
        "perfect_day",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=[
            "app.workers.tasks",
            "app.workers.beat_tasks",
        ],
    )

    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        beat_schedule={
            "dispatch-due-scans": {
                "task": "app.workers.beat_tasks.dispatch_due_scans",
                "schedule": 300,  # every 5 minutes
            },
            "process-hard-deletes": {
                "task": "app.workers.beat_tasks.process_hard_deletes",
                "schedule": 3600,  # every hour
            },
            "sweep-orphaned-photos": {
                "task": "app.workers.beat_tasks.sweep_orphaned_photos",
                "schedule": timedelta(hours=6),
            },
        },
    )

    return app


celery_app = create_celery()
