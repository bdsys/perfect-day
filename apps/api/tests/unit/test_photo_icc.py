"""Colour profiles survive the location strip and the HEIC display copy (item 14).

An ICC profile carries no location, but dropping it makes wide-gamut photos
(iPhone Display P3) look duller. GPS and EXIF must still be gone.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageCms

from app.services.photos import make_display_jpeg, strip_location

XMP = b"<x:xmpmeta><exif:GPSLatitude>47,36N</exif:GPSLatitude></x:xmpmeta>"


def _icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _gps_exif() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (47.0, 36.0, 0.0)
    gps[3], gps[4] = "W", (122.0, 18.0, 0.0)
    return exif.tobytes()


def _image(fmt: str, **kwargs) -> bytes:
    import pillow_heif

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 120, 200)).save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    import pillow_heif

    pillow_heif.register_heif_opener()
    return Image.open(io.BytesIO(data))


def test_display_copy_keeps_icc_and_drops_exif():
    icc = _icc()
    heic = _image("HEIF", exif=_gps_exif(), icc_profile=icc)
    assert _open(heic).info.get("icc_profile") == icc  # the source really has one

    out = make_display_jpeg(heic, "image/heic")

    assert out is not None
    img = _open(out)
    assert img.info.get("icc_profile") == icc
    assert not dict(img.getexif().get_ifd(0x8825))
    assert 0x010F not in img.getexif()  # no camera EXIF either


def test_display_copy_without_icc_has_none():
    out = make_display_jpeg(_image("HEIF"), "image/heic")
    assert out is not None
    assert "icc_profile" not in _open(out).info


@pytest.mark.parametrize(
    ("fmt", "mime", "extra"),
    [
        ("JPEG", "image/jpeg", {"xmp": XMP}),
        ("PNG", "image/png", {}),
        ("WEBP", "image/webp", {}),
        ("HEIF", "image/heic", {}),
    ],
)
def test_strip_location_keeps_icc_but_drops_gps_and_exif(fmt, mime, extra):
    icc = _icc()
    data = _image(fmt, exif=_gps_exif(), icc_profile=icc, **extra)
    assert _open(data).info.get("icc_profile") == icc

    out = strip_location(data, mime)

    img = _open(out)
    assert img.info.get("icc_profile") == icc
    assert not dict(img.getexif().get_ifd(0x8825))
    assert b"GPSLatitude" not in out
    if fmt != "JPEG":  # JPEG deliberately keeps non-location EXIF; the others keep none
        assert 0x010F not in img.getexif()
