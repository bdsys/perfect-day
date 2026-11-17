"""Shared helpers for photo integration tests."""
from __future__ import annotations

import io
import pathlib

from PIL import Image

PASSWORD = "Password1!"  # noqa: S105


def read_fixture(name: str) -> bytes:
    return (pathlib.Path(__file__).parent.parent / "fixtures" / "photos" / name).read_bytes()


async def register_and_login(client, email: str) -> dict[str, str]:
    """Register a user, log in, and return the Authorization header."""
    r = await client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def jpeg_with_exif(taken="2025:08:14 13:45:00", lat=37.7749, lon=-122.4194) -> bytes:
    """A tiny JPEG carrying EXIF DateTimeOriginal and GPS."""
    from io import BytesIO

    import piexif
    from PIL import Image

    def dms(d):
        d = abs(d)
        deg = int(d)
        m = int((d - deg) * 60)
        return ((deg, 1), (m, 1), (round(((d - deg) * 60 - m) * 60 * 100), 100))

    exif = {
        "0th": {},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: taken.encode("ascii")},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: dms(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: dms(lon),
        },
        "1st": {},
        "thumbnail": None,
    }
    buf = BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="JPEG", exif=piexif.dump(exif))
    return buf.getvalue()


async def upload_photo(client, headers, body: bytes, content_type: str = "image/jpeg") -> str:
    """Upload raw bytes through POST /v1/photos; returns the photo id."""
    r = await client.post(
        "/v1/photos", content=body, headers={**headers, "Content-Type": content_type}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def heic_bytes() -> bytes:
    import pillow_heif

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 120, 200)).save(buf, format="HEIF")
    return buf.getvalue()


def gps_heic_bytes() -> bytes:
    """A HEIC whose EXIF carries GPS coordinates (47.6 N, 122.3 W)."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (47.0, 36.0, 0.0)
    gps[3], gps[4] = "W", (122.0, 18.0, 0.0)
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 120, 200)).save(buf, format="HEIF", exif=exif.tobytes())
    return buf.getvalue()


def gps_of(data: bytes) -> dict:
    return dict(Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825))
