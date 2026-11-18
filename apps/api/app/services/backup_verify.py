"""Check that restored photos and avatars decrypt with the current keys (row 36 drill).

A restore can look complete (rows back, objects back) and still be useless if
MASTER_SECRET is wrong or objects came back damaged. This decrypts a sample the
way the API would. Failure messages name the kind and id only, never content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.photo_crypto import PURPOSE_FULL, decrypt_stream, unwrap_dek
from app.models import Photo, User
from app.services.avatar import load_avatar
from app.services.photos import get_object_bytes


@dataclass
class VerifyReport:
    checked: int = 0
    failures: list[str] = field(default_factory=list)


def _reason(exc: Exception) -> str:
    return type(exc).__name__


def verify_photo_sample(db: Session, limit: int = 20) -> VerifyReport:
    """Decrypt every user's avatar and up to *limit* of the newest finalized photos."""
    report = VerifyReport()

    users = db.execute(
        select(User.id, User.avatar_s3_key, User.avatar_dek_ciphertext).where(
            User.avatar_s3_key.is_not(None)
        )
    ).all()
    for user_id, key, wrapped in users:
        report.checked += 1
        try:
            if wrapped is None:
                raise ValueError("no wrapped key")
            load_avatar(key, wrapped, user_id)
        except Exception as exc:  # noqa: BLE001  # every failure is reported, none is fatal
            report.failures.append(f"avatar {user_id}: {_reason(exc)}")

    photos = db.execute(
        select(Photo.id, Photo.user_id, Photo.s3_key, Photo.dek_ciphertext)
        .where(Photo.deleted_at.is_(None), Photo.finalized_at.is_not(None))
        .order_by(Photo.created_at.desc())
        .limit(limit)
    ).all()
    for photo_id, user_id, key, wrapped in photos:
        report.checked += 1
        try:
            if wrapped is None:
                raise ValueError("no wrapped key")
            decrypt_stream(get_object_bytes(key), unwrap_dek(wrapped, user_id), PURPOSE_FULL)
        except Exception as exc:  # noqa: BLE001
            report.failures.append(f"photo {photo_id}: {_reason(exc)}")

    return report


def referenced_keys(db: Session) -> set[str]:
    """Every object key the live data needs: finalized, not-deleted photos (original,
    thumbnail, display) and the avatars of users who are not deleted."""
    keys: set[str] = set()
    photos = db.execute(
        select(Photo.s3_key, Photo.thumbnail_s3_key, Photo.display_s3_key).where(
            Photo.deleted_at.is_(None), Photo.finalized_at.is_not(None)
        )
    ).all()
    for row in photos:
        keys.update(key for key in row if key)
    avatars = db.execute(
        select(User.avatar_s3_key).where(User.avatar_s3_key.is_not(None), User.deleted_at.is_(None))
    ).scalars()
    keys.update(key for key in avatars if key)
    return keys


def missing_referenced_keys(db: Session, s3, bucket: str) -> list[str]:
    """Referenced keys absent from *bucket* (a full listing, not a sample), sorted.

    The storage switch refuses to copy from a store that lacks any of them: copying
    an incomplete store exactly would delete the photos from the other one (row 41).
    """
    present: set[str] = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        present.update(obj["Key"] for obj in page.get("Contents", []))
    return sorted(referenced_keys(db) - present)


def has_photo_or_avatar_rows(db: Session) -> bool:
    """True if any photo row or avatar exists, so checking nothing would be a failure."""
    photo = db.execute(select(Photo.id).limit(1)).first()
    avatar = db.execute(select(User.id).where(User.avatar_s3_key.is_not(None)).limit(1)).first()
    return photo is not None or avatar is not None
