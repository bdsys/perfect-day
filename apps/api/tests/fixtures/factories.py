"""Factory functions for creating test fixtures in the database."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Diary, Entry, Event, ScanJob, User


async def make_user(
    db: AsyncSession,
    email: str | None = None,
    password: str = "Password1!",
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
    entry: Entry,
    source: str = "google_calendar",
    external_id: str | None = None,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    ev = Event(
        entry_id=entry.id,
        diary_id=entry.diary_id,
        source=source,
        external_id=external_id or uuid.uuid4().hex,
        payload=payload or {"summary": "Test event", "location": ""},
        occurred_at=occurred_at or datetime.now(tz=timezone.utc),
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev
