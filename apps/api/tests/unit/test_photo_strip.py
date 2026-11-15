"""Shared (display) JPEG copies keep only Orientation from EXIF (TR-30).

Make, MakerNote, GPS and the IFD1 thumbnail can all identify a device or carry a
location, so none of them survive the strip. Orientation stays so the photo displays upright.
"""

from __future__ import annotations

import io
import struct

from PIL import Image, ImageCms

from app.services.photos import strip_location


def _thumb_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, format="JPEG")
    return buf.getvalue()


def _exif_with_thumbnail() -> bytes:
    """Orientation 6, Make, MakerNote, GPS and a real IFD1 holding an embedded JPEG.

    Pillow cannot write IFD1 thumbnail data, so Pillow writes IFD0/Exif/GPS and we append
    an IFD1 by hand, pointing IFD0's next-IFD offset at it.
    """
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "Apple"
    exif.get_ifd(0x8769)[0x927C] = b"MAKERNOTE-DATA"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (47.0, 36.0, 0.0)
    gps[3], gps[4] = "W", (122.0, 18.0, 0.0)
    raw = bytearray(exif.tobytes())  # b"Exif\0\0" + TIFF
    tiff = raw[6:]
    endian = "<" if tiff[:2] == b"II" else ">"
    n = struct.unpack_from(endian + "H", tiff, 8)[0]
    next_ptr_at = 8 + 2 + n * 12
    assert struct.unpack_from(endian + "I", tiff, next_ptr_at)[0] == 0
    ifd1_at = len(tiff)
    thumb = _thumb_jpeg()
    thumb_at = ifd1_at + 2 + 2 * 12 + 4
    ifd1 = struct.pack(endian + "H", 2)
    ifd1 += struct.pack(endian + "HHII", 0x0201, 4, 1, thumb_at)  # JPEGInterchangeFormat
    ifd1 += struct.pack(endian + "HHII", 0x0202, 4, 1, len(thumb))  # ...Length
    ifd1 += struct.pack(endian + "I", 0) + thumb
    struct.pack_into(endian + "I", tiff, next_ptr_at, ifd1_at)
    return bytes(raw[:6] + tiff + ifd1)


def _jpeg(exif: bytes | None, **kw) -> bytes:
    buf = io.BytesIO()
    extra = {"exif": exif} if exif else {}
    Image.new("RGB", (32, 24), (10, 120, 200)).save(buf, format="JPEG", **extra, **kw)
    return buf.getvalue()


def test_jpeg_keeps_only_orientation():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _jpeg(_exif_with_thumbnail(), icc_profile=icc)
    # The source really carries everything we claim to remove.
    s = Image.open(io.BytesIO(src))
    assert s.getexif()[0x010F] == "Apple"
    assert s.getexif().get_ifd(0x8769).get(0x927C)
    assert s.getexif().get_ifd(0x8825)
    assert _thumb_jpeg() in s.info["exif"]  # the IFD1 thumbnail is embedded in the EXIF
    assert b"Apple" in src and b"MAKERNOTE-DATA" in src

    out = strip_location(src, "image/jpeg")

    img = Image.open(io.BytesIO(out))
    exif = img.getexif()
    assert set(exif.keys()) == {0x0112}
    assert exif[0x0112] == 6
    assert not dict(exif.get_ifd(0x8825))
    assert not dict(exif.get_ifd(0x8769))
    assert "exif" not in img.info or _thumb_jpeg() not in img.info["exif"]
    assert b"Apple" not in out
    assert b"MAKERNOTE-DATA" not in out
    # No JPEG start marker nested inside the APP1 (EXIF) segment, i.e. no thumbnail.
    app1_at = out.index(b"\xff\xe1")
    app1_len = struct.unpack(">H", out[app1_at + 2 : app1_at + 4])[0]
    assert b"\xff\xd8" not in out[app1_at + 4 : app1_at + 2 + app1_len]
    assert img.info.get("icc_profile") == icc  # colour profile kept


def test_jpeg_without_exif_stays_without():
    out = strip_location(_jpeg(None), "image/jpeg")

    img = Image.open(io.BytesIO(out))
    assert len(img.getexif()) == 0
    assert b"Exif\x00\x00" not in out


def test_mpo_keeps_orientation_and_becomes_plain_jpeg():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    exif[0x0112] = 6
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (47.0, 36.0, 0.0)
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(
        buf, "MPO", save_all=True, append_images=[Image.new("RGB", (8, 8))],
        exif=exif.tobytes(), icc_profile=icc,
    )
    src = buf.getvalue()
    assert Image.open(io.BytesIO(src)).format == "MPO"

    out = strip_location(src, "image/jpeg")

    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert set(img.getexif().keys()) == {0x0112}
    assert img.getexif()[0x0112] == 6
    assert not dict(img.getexif().get_ifd(0x8825))
    assert b"MPF" not in out
    assert img.info.get("icc_profile") == icc
