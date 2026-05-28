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
