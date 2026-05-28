"""Sync helpers for photo upload/encryption/thumbnailing.

No DB or SQLAlchemy access here. boto3 is sync, so all S3 ops are sync calls
that callers may run via run_in_executor when needed.
"""
from __future__ import annotations

ALLOWED_MIME: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/webp",
    "image/gif",
    "image/avif",
})

MAX_BYTES: int = 50 * 1024 * 1024
THUMBNAIL_LONGEST_EDGE: int = 512
THUMBNAIL_QUALITY: int = 80
PRESIGN_TTL_SECONDS: int = 900


def detect_mime(head: bytes) -> str | None:
    """Sniff image MIME from the first ~32 bytes. Returns None on unknown."""
    if not head:
        return None
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"hevx"):
            return "image/heic"
        if brand == b"heif":
            return "image/heif"
        if brand in (b"avif", b"avis"):
            return "image/avif"
    return None
