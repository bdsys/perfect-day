"""Verify sweep_orphaned_photos is in the Celery beat schedule."""


def test_sweep_orphaned_photos_in_beat_schedule():
    from app.workers.celery_app import celery_app
    sched = celery_app.conf.beat_schedule
    assert "sweep-orphaned-photos" in sched, f"sweep-orphaned-photos not in {list(sched.keys())}"
    entry = sched["sweep-orphaned-photos"]
    assert "sweep_orphaned_photos" in entry["task"]
    assert entry["schedule"].total_seconds() == 6 * 3600
