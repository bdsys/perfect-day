"""Factory functions for creating test fixtures in the database."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Diary, Entry, EntryPhoto, Event, Photo, ScanJob, User


async def make_user(
    db: AsyncSession,
    email: str | None = None,
    password: str = "Password1!",  # noqa: S107
    display_name: str | None = None,
    subscription_tier: str = "free",
) -> User:
    u = User(
        email=email or f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(password),
        display_name=display_name,
        subscription_tier=subscription_tier,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def make_diary(
    db: AsyncSession,
    owner: User,
    name: str = "Test Diary",
    timezone: str = "America/New_York",
) -> Diary:
    slug = name.lower().replace(" ", "-")
    d = Diary(
        owner_user_id=owner.id,
        name=name,
        slug=slug,
        timezone=timezone,
        tone_hint="warm, narrative",
        subject_relation="self",
    )
    db.add(d)
    # Also create the ScanJob row
    scan_job = ScanJob(diary=d)
    db.add(scan_job)
    await db.commit()
    await db.refresh(d)
    return d


async def make_entry(
    db: AsyncSession,
    diary: Diary,
    entry_date: date | None = None,
    title: str | None = "Test Entry",
    body_markdown: str | None = "Some content.",
    status: str = "draft",
) -> Entry:
    e = Entry(
        diary_id=diary.id,
        entry_date=entry_date or date.today(),
        title=title,
        body_markdown=body_markdown,
        status=status,
        created_by="manual",
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def make_event(
    db: AsyncSession,
    diary_id: uuid.UUID | None = None,
    entry: Entry | None = None,
    source: str = "google_calendar",
    external_id: str | None = None,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    ev = Event(
        diary_id=diary_id if diary_id is not None else getattr(entry, "diary_id", None),
        entry_id=entry.id if entry is not None else None,
        source=source,
        external_id=external_id or uuid.uuid4().hex,
        payload=payload
            or {
                "summary": "Test event",
                "location": "",
                "description": "",
                "start": {},
                "end": {},
                "status": "",
                "attendees": [],
            },
        occurred_at=occurred_at or datetime.now(tz=UTC),
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev


async def make_photo(
    db: AsyncSession,
    *,
    user: User,
    finalized: bool = True,
    source: str = "upload",
    mime_type: str | None = "image/jpeg",
    size_bytes: int | None = 1024,
    s3_key: str | None = None,
    thumbnail_s3_key: str | None = None,
    dek_ciphertext: bytes | None = None,
    taken_at: datetime | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> Photo:
    from app.core.photo_crypto import generate_dek, wrap_dek

    pid = uuid.uuid4()
    photo = Photo(
        id=pid,
        user_id=user.id,
        s3_key=s3_key or f"{user.id}/{pid}.enc",
        mime_type=mime_type if finalized else None,
        bytes=size_bytes if finalized else None,
        finalized_at=datetime.now(tz=UTC) if finalized else None,
        dek_ciphertext=dek_ciphertext or (wrap_dek(generate_dek(), user.id) if finalized else None),
        source=source,
        taken_at=taken_at,
        lat=lat,
        lon=lon,
    )
    if finalized:
        photo.thumbnail_s3_key = thumbnail_s3_key or f"{user.id}/{pid}_thumb.enc"
    db.add(photo)
    await db.flush()
    return photo


async def make_entry_photo(
    db: AsyncSession, *, entry: Entry, photo: Photo, position: int | None = None
) -> EntryPhoto:
    ep = EntryPhoto(entry_id=entry.id, photo_id=photo.id, position=position)
    db.add(ep)
    await db.flush()
    return ep
