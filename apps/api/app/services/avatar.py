"""Profile photos: a 256 x 256 JPEG with no metadata, encrypted like entry photos.

The picture may well be of a child, so it gets the same care as entry photos: every
piece of metadata (location included) is dropped by re-encoding, and the bytes are
stored encrypted under a per-object data key wrapped for the user. Sync, no DB access:
callers run these in an executor.
"""

from __future__ import annotations

import uuid
from io import BytesIO

import structlog

from app.core.photo_crypto import (
    PURPOSE_AVATAR,
    decrypt_stream,
    encrypt_stream,
    generate_dek,
    unwrap_dek,
    wrap_dek,
)
from app.services.photo_ingest import UnsupportedPhoto
from app.services.photos import (
    ALLOWED_MIME,
    delete_object,
    detect_mime,
    get_object_bytes,
    put_object_bytes,
)

log = structlog.get_logger()

AVATAR_SIZE = 256
AVATAR_QUALITY = 85


def make_avatar_jpeg(data: bytes) -> bytes:
    """Upright, centre-cropped, AVATAR_SIZE square JPEG of *data*, with no metadata.

    Raises UnsupportedPhoto for anything that is not an allowed image type or that
    Pillow cannot decode (truncated files and decompression bombs included).
    """
    mime = detect_mime(data[:32])
    if mime not in ALLOWED_MIME:
        raise UnsupportedPhoto(mime or "unknown")

    import pillow_heif  # type: ignore[import-untyped]
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    try:
        with Image.open(BytesIO(data)) as img:
            # JPEGs decode at a reduced scale (still at least 2x the target) to spare memory.
            img.draft("RGB", (2 * AVATAR_SIZE, 2 * AVATAR_SIZE))
            img.load()
            upright = ImageOps.exif_transpose(img) or img
            if upright.mode in ("RGBA", "LA", "PA") or "transparency" in upright.info:
                # Transparent areas become white rather than black.
                rgba = upright.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                upright = flat
            square = ImageOps.fit(
                upright, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS, centering=(0.5, 0.5)
            )
            rgb = square if square.mode == "RGB" else square.convert("RGB")
            rgb.info = {}  # belt and braces: nothing from the source rides along
            out = BytesIO()
            # No exif / icc_profile / xmp argument: the re-encoded file carries no metadata.
            rgb.save(out, format="JPEG", quality=AVATAR_QUALITY)
            return out.getvalue()
    except Exception as exc:  # any decode failure, incl. Image.DecompressionBombError
        raise UnsupportedPhoto(mime) from exc


def store_avatar(user_id: uuid.UUID, jpeg: bytes) -> tuple[str, bytes]:
    """Encrypt *jpeg* under a fresh data key and store it; returns (s3_key, wrapped_dek)."""
    dek = generate_dek()
    wrapped = wrap_dek(dek, user_id)
    key = f"{user_id}/avatar/{uuid.uuid4().hex}.jpg"
    put_object_bytes(key, encrypt_stream(jpeg, dek, PURPOSE_AVATAR))
    return key, wrapped


def load_avatar(s3_key: str, wrapped_dek: bytes, user_id: uuid.UUID) -> bytes:
    """The decrypted JPEG stored at *s3_key*."""
    dek = unwrap_dek(wrapped_dek, user_id)
    return decrypt_stream(get_object_bytes(s3_key), dek, PURPOSE_AVATAR)


def delete_avatar_object(s3_key: str) -> None:
    """Delete a stored avatar. Storage errors are logged and swallowed: the row is
    already saved, and account hard delete scrubs `{user_id}/` anyway."""
    try:
        delete_object(s3_key)
    except Exception as exc:  # noqa: BLE001
        # Class only: the key names the user and the message can echo it.
        log.warning("avatar_delete_failed", error_class=type(exc).__name__)
