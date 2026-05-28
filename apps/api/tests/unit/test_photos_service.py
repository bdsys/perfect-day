"""Unit tests for app.services.photos — pure helpers, no I/O."""
from __future__ import annotations

import pytest


def test_detect_mime_jpeg():
    from app.services.photos import detect_mime
    assert detect_mime(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"


def test_detect_mime_png():
    from app.services.photos import detect_mime
    assert detect_mime(b"\x89PNG\r\n\x1a\n") == "image/png"


def test_detect_mime_gif87a():
    from app.services.photos import detect_mime
    assert detect_mime(b"GIF87a") == "image/gif"


def test_detect_mime_gif89a():
    from app.services.photos import detect_mime
    assert detect_mime(b"GIF89a") == "image/gif"


def test_detect_mime_webp():
    from app.services.photos import detect_mime
    head = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 "
    assert detect_mime(head) == "image/webp"


def test_detect_mime_heic():
    from app.services.photos import detect_mime
    head = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8
    assert detect_mime(head) == "image/heic"


def test_detect_mime_avif():
    from app.services.photos import detect_mime
    head = b"\x00\x00\x00\x18ftypavif" + b"\x00" * 8
    assert detect_mime(head) == "image/avif"


def test_detect_mime_unknown_returns_none():
    from app.services.photos import detect_mime
    assert detect_mime(b"%PDF-1.4\n") is None
    assert detect_mime(b"") is None


def test_constants():
    from app.services.photos import (
        ALLOWED_MIME, MAX_BYTES, THUMBNAIL_LONGEST_EDGE, THUMBNAIL_QUALITY, PRESIGN_TTL_SECONDS,
    )
    assert "image/jpeg" in ALLOWED_MIME
    assert "image/heic" in ALLOWED_MIME
    assert MAX_BYTES == 50 * 1024 * 1024
    assert THUMBNAIL_LONGEST_EDGE == 512
    assert THUMBNAIL_QUALITY == 80
    assert PRESIGN_TTL_SECONDS == 900


def _build_jpeg_with_exif(taken="2025:08:14 13:45:00", lat=37.7749, lon=-122.4194):
    """Build a tiny in-memory JPEG with EXIF DateTimeOriginal + GPS."""
    from io import BytesIO
    import piexif
    from PIL import Image

    img = Image.new("RGB", (4, 4), (255, 0, 0))
    def deg_to_dms(d):
        d = abs(d)
        deg = int(d)
        m = int((d - deg) * 60)
        s = round(((d - deg) * 60 - m) * 60 * 100)
        return ((deg, 1), (m, 1), (s, 100))

    exif = {
        "0th": {},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: taken.encode("ascii")},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: deg_to_dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: deg_to_dms(lon),
        },
        "1st": {},
        "thumbnail": None,
    }
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=piexif.dump(exif))
    return buf.getvalue()


def test_parse_exif_extracts_date_and_gps():
    from app.services.photos import parse_exif
    img = _build_jpeg_with_exif()
    result = parse_exif(img)
    assert result["taken_at"] is not None
    assert result["taken_at"].year == 2025
    assert result["taken_at"].month == 8
    assert result["lat"] is not None and abs(result["lat"] - 37.7749) < 0.001
    assert result["lon"] is not None and abs(result["lon"] - -122.4194) < 0.001


def test_parse_exif_returns_none_on_no_exif():
    from io import BytesIO
    from PIL import Image
    from app.services.photos import parse_exif

    img = Image.new("RGB", (4, 4))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    result = parse_exif(buf.getvalue())
    assert result == {"taken_at": None, "lat": None, "lon": None}


def test_parse_exif_returns_none_on_garbage():
    from app.services.photos import parse_exif
    assert parse_exif(b"not an image") == {"taken_at": None, "lat": None, "lon": None}


def test_parse_exif_clamps_invalid_gps():
    """GPS that decodes to NaN or out-of-range should yield None lat/lon."""
    from app.services.photos import parse_exif
    img = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    result = parse_exif(img)
    assert result["lat"] is None and result["lon"] is None


def test_generate_thumbnail_jpeg_round_trip():
    from io import BytesIO
    from PIL import Image
    from app.services.photos import generate_thumbnail

    src = Image.new("RGB", (1024, 768), (10, 200, 50))
    buf = BytesIO()
    src.save(buf, format="JPEG", quality=90)
    out = generate_thumbnail(buf.getvalue(), "image/jpeg")
    assert out[:3] == b"\xff\xd8\xff"  # JPEG magic
    img = Image.open(BytesIO(out))
    assert max(img.size) <= 512


def test_generate_thumbnail_preserves_orientation():
    from io import BytesIO
    from PIL import Image
    from app.services.photos import generate_thumbnail

    src = Image.new("RGB", (200, 100), (255, 0, 0))
    buf = BytesIO()
    src.save(buf, format="PNG")
    out = generate_thumbnail(buf.getvalue(), "image/png")
    img = Image.open(BytesIO(out))
    assert img.format == "JPEG"


def test_generate_thumbnail_raises_on_garbage():
    import pytest
    from app.services.photos import generate_thumbnail
    with pytest.raises(ValueError):
        generate_thumbnail(b"not an image", "image/jpeg")
