from __future__ import annotations

from datetime import timedelta

from celery import Celery
from celery.schedules import crontab
from celery.signals import after_setup_logger, after_setup_task_logger

from app.core.config import get_settings
from app.core.logging import quiet_http_loggers


def create_celery() -> Celery:
    settings = get_settings()

    app = Celery(
        "perfect_day",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=[
            "app.workers.tasks",
            "app.workers.beat_tasks",
            "app.workers.model_check",
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
            "check-anthropic-model": {
                "task": "app.workers.model_check.check_anthropic_model",
                # A clock time, not an interval: an interval restarts from zero
                # whenever beat restarts, so frequent deploys would starve it.
                "schedule": crontab(minute=0, hour=6, day_of_week="mon"),
            },
            "sweep-orphaned-photos": {
                "task": "app.workers.beat_tasks.sweep_orphaned_photos",
                "schedule": timedelta(hours=6),
            },
        },
    )

    return app


celery_app = create_celery()


# The worker never runs configure_logging, and `--loglevel=info` sets the root
# logger to INFO: keep httpx from logging Google base URLs after Celery has set
# up its loggers.
@after_setup_logger.connect
def on_after_setup_logger(logger=None, **kwargs) -> None:
    quiet_http_loggers()


@after_setup_task_logger.connect
def on_after_setup_task_logger(logger=None, **kwargs) -> None:
    quiet_http_loggers()


quiet_http_loggers()
