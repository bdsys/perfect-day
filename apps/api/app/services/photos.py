"""Sync helpers for photo upload/encryption/thumbnailing.

No DB or SQLAlchemy access here. boto3 is sync, so all S3 ops are sync calls
that callers may run via run_in_executor when needed.
"""
from __future__ import annotations

import datetime
import math
from io import BytesIO

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



def _exif_dms_to_decimal(dms, ref: str) -> float | None:
    """Convert EXIF DMS to decimal degrees.

    Handles both Pillow's flat float tuple (deg, min, sec) returned by
    get_ifd(), and raw rational tuples ((num, denom), ...) from other sources.
    """
    try:
        def _to_float(v) -> float:
            if isinstance(v, tuple):
                return v[0] / v[1]
            return float(v)

        deg = _to_float(dms[0])
        minutes = _to_float(dms[1])
        seconds = _to_float(dms[2])
        val = deg + minutes / 60 + seconds / 3600
        if ref in ("S", "W"):
            val = -val
        if math.isnan(val) or math.isinf(val):
            return None
        if ref in ("N", "S") and not (-90 <= val <= 90):
            return None
        if ref in ("E", "W") and not (-180 <= val <= 180):
            return None
        return val
    except (ZeroDivisionError, IndexError, TypeError, ValueError):
        return None


def parse_exif(image_bytes: bytes) -> dict:
    """Extract DateTimeOriginal + GPS from EXIF. Any error → all None."""
    out: dict = {"taken_at": None, "lat": None, "lon": None}
    try:
        from PIL import ExifTags, Image

        img = Image.open(BytesIO(image_bytes))
        exif = img.getexif()
        if not exif:
            return out

        # DateTimeOriginal (36867); fall back to DateTime (306)
        ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(ExifTags, "IFD") else exif
        dt_str = ifd.get(36867) or exif.get(306)
        offset = ifd.get(36881)  # OffsetTimeOriginal, e.g. "+02:00"
        if dt_str:
            try:
                dt = datetime.datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                if offset and len(offset) >= 6:
                    sign = 1 if offset[0] == "+" else -1
                    hh = int(offset[1:3])
                    mm = int(offset[4:6])
                    tz = datetime.timezone(sign * datetime.timedelta(hours=hh, minutes=mm))
                else:
                    tz = datetime.UTC
                out["taken_at"] = dt.replace(tzinfo=tz)
            except (ValueError, TypeError):
                pass

        # GPS via GPSInfo IFD (34853)
        gps_ifd = None
        if hasattr(ExifTags, "IFD"):
            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            except (KeyError, AttributeError):
                gps_ifd = None
        if gps_ifd:
            lat_dms, lat_ref = gps_ifd.get(2), gps_ifd.get(1)
            lon_dms, lon_ref = gps_ifd.get(4), gps_ifd.get(3)
            if lat_dms and lat_ref:
                out["lat"] = _exif_dms_to_decimal(lat_dms, lat_ref)
            if lon_dms and lon_ref:
                out["lon"] = _exif_dms_to_decimal(lon_dms, lon_ref)
    except Exception:
        return {"taken_at": None, "lat": None, "lon": None}
    return out


# Register HEIF opener at module load.
try:
    import pillow_heif  # type: ignore[import-untyped]
    pillow_heif.register_heif_opener()
except ImportError:
    pass


def strip_location(data: bytes, mime: str) -> bytes:
    """Return the image with its GPS location removed (other EXIF is kept for JPEG).

    JPEG: re-saved at the original quality with the EXIF minus the GPS IFD.
    Other types: re-saved in the same format without metadata.
    Raises ValueError if the bytes are not a readable image.
    """
    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            fmt = img.format or "JPEG"
            out = BytesIO()
            if fmt == "JPEG":
                exif = img.getexif()
                exif.pop(0x8825, None)
                img.save(out, "JPEG", quality="keep", exif=exif)
            else:
                img.save(out, fmt)
            return out.getvalue()
    except Exception as exc:
        raise ValueError("unreadable_image") from exc


def generate_thumbnail(image_bytes: bytes, mime: str) -> bytes:
    """Decode → EXIF-rotate → resize ≤512 px longest edge → JPEG q=80.

    Raises ValueError on undecodable input.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img: Image.Image = Image.open(BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img) or img
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail(
            (THUMBNAIL_LONGEST_EDGE, THUMBNAIL_LONGEST_EDGE),
            Image.Resampling.LANCZOS,
        )
        out = BytesIO()
        img.save(out, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
        return out.getvalue()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError(f"undecodable image: {e}") from e


DISPLAY_QUALITY: int = 90
_NEEDS_DISPLAY_COPY = frozenset({"image/heic", "image/heif"})


def make_display_jpeg(data: bytes, mime: str) -> bytes | None:
    """A full-size JPEG of a HEIC/HEIF photo, for browsers that cannot show HEIC.

    Returns None for types browsers already display. The JPEG is built without
    any EXIF, so it carries no location and is safe to show to anyone.
    Raises ValueError on undecodable input.
    """
    if mime not in _NEEDS_DISPLAY_COPY:
        return None
    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            img = ImageOps.exif_transpose(img) or img
            if img.mode != "RGB":
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, format="JPEG", quality=DISPLAY_QUALITY)
            return out.getvalue()
    except Exception as e:
        raise ValueError("unreadable_image") from e


# ---------------------------------------------------------------------------
# S3 wrappers
# ---------------------------------------------------------------------------


def _bucket() -> str:
    from app.core.config import get_settings
    return get_settings().s3_bucket_photos


def put_object_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
    from app.core.dependencies import get_s3
    get_s3().put_object(Bucket=_bucket(), Key=key, Body=body, ContentType=content_type)


def get_object_bytes(key: str) -> bytes:
    from app.core.dependencies import get_s3
    return get_s3().get_object(Bucket=_bucket(), Key=key)["Body"].read()


def head_object_size(key: str) -> int | None:
    from botocore.exceptions import ClientError

    from app.core.dependencies import get_s3
    try:
        resp = get_s3().head_object(Bucket=_bucket(), Key=key)
        return int(resp["ContentLength"])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def delete_object(key: str) -> None:
    from botocore.exceptions import ClientError

    from app.core.dependencies import get_s3
    try:
        get_s3().delete_object(Bucket=_bucket(), Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return
        raise


def stream_object(key: str):
    """Return botocore StreamingBody for *key*. Caller is responsible for closing."""
    from app.core.dependencies import get_s3
    return get_s3().get_object(Bucket=_bucket(), Key=key)["Body"]
