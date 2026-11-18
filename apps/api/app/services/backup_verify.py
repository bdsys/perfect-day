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


def has_photo_or_avatar_rows(db: Session) -> bool:
    """True if any photo row or avatar exists, so checking nothing would be a failure."""
    photo = db.execute(select(Photo.id).limit(1)).first()
    avatar = db.execute(select(User.id).where(User.avatar_s3_key.is_not(None)).limit(1)).first()
    return photo is not None or avatar is not None
