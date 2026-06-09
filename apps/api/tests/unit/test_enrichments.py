import uuid
from datetime import date
from decimal import Decimal


def _fake_entry(entry_date: date, end_date=None, photo_lat=None, photo_lon=None,
                diary_lat=None, diary_lon=None):
    from types import SimpleNamespace
    photos = []
    if photo_lat is not None:
        photos.append(SimpleNamespace(lat=Decimal(str(photo_lat)), lon=Decimal(str(photo_lon))))
    diary = SimpleNamespace(
        id=uuid.uuid4(),
        lat=Decimal(str(diary_lat)) if diary_lat is not None else None,
        lon=Decimal(str(diary_lon)) if diary_lon is not None else None,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        entry_date=entry_date,
        entry_end_date=end_date,
        photos=photos,
        diary_id=diary.id,
        diary=diary,
    )


def test_iter_entry_dates_single():
    from app.workers.enrichments import _iter_entry_dates
    e = _fake_entry(date(2026, 5, 29))
    assert list(_iter_entry_dates(e)) == [date(2026, 5, 29)]


def test_iter_entry_dates_range():
    from app.workers.enrichments import _iter_entry_dates
    e = _fake_entry(date(2026, 5, 29), end_date=date(2026, 5, 31))
    assert list(_iter_entry_dates(e)) == [date(2026, 5, 29), date(2026, 5, 30), date(2026, 5, 31)]


def test_iter_entry_dates_cap_at_30_days():
    from app.workers.enrichments import _iter_entry_dates
    e = _fake_entry(date(2026, 1, 1), end_date=date(2026, 12, 31))
    out = list(_iter_entry_dates(e))
    assert len(out) == 30


def test_resolve_lat_lon_prefers_photo_exif():
    from app.workers.enrichments import _resolve_lat_lon
    e = _fake_entry(date(2026, 5, 29), photo_lat=10.5, photo_lon=20.5,
                    diary_lat=40.0, diary_lon=-80.0)
    result = _resolve_lat_lon(e)
    assert result == (10.5, 20.5)


def test_resolve_lat_lon_falls_back_to_diary():
    from app.workers.enrichments import _resolve_lat_lon
    e = _fake_entry(date(2026, 5, 29), diary_lat=40.0, diary_lon=-80.0)
    assert _resolve_lat_lon(e) == (40.0, -80.0)


def test_resolve_lat_lon_returns_none_when_unset():
    from app.workers.enrichments import _resolve_lat_lon
    e = _fake_entry(date(2026, 5, 29))
    assert _resolve_lat_lon(e) is None
