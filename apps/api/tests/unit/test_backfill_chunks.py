"""Unit tests for _iter_week_chunks helper."""

from datetime import date

from app.workers.backfill import _iter_week_chunks


def test_chunks_15_day_range():
    chunks = list(_iter_week_chunks(date(2026, 5, 1), date(2026, 5, 15)))
    assert chunks == [
        (date(2026, 5, 1), date(2026, 5, 8)),
        (date(2026, 5, 8), date(2026, 5, 15)),
    ]


def test_chunks_exact_week_boundary():
    chunks = list(_iter_week_chunks(date(2026, 5, 1), date(2026, 5, 8)))
    assert chunks == [(date(2026, 5, 1), date(2026, 5, 8))]


def test_chunks_single_day():
    chunks = list(_iter_week_chunks(date(2026, 5, 1), date(2026, 5, 1)))
    assert chunks == [(date(2026, 5, 1), date(2026, 5, 1))]
