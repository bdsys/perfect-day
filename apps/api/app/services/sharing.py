"""Rules for sharing a diary: invite tokens, people limits, the downgraded-editor role."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models import Diary, DiaryPermission, Invitation, User
from app.services.tier import limits_for, tier_limit_error

INVITE_TTL = timedelta(days=7)


def new_invite_token() -> tuple[str, str]:
    """A fresh invite token: (raw, sha256 hex). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def mask_email(email: str) -> str:
    """'mom@example.com' becomes 'm•••@example.com'."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}"


def effective_role(stored_role: str, owner_tier: str) -> Literal["editor", "viewer"]:
    """An editor acts as a viewer unless the owner's plan allows editors."""
    if stored_role == "editor" and limits_for(owner_tier).editors:
        return "editor"
    return "viewer"


def person_name(user: User) -> str:
    name = (user.display_name or "").strip()
    return name or user.email


async def people_counts(
    db: AsyncSession,
    diary_ids: list[uuid.UUID],
    *,
    exclude_invitation_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, int]:
    """People per diary: member rows plus pending (unaccepted, unexpired) invitations.

    The one definition of the count; two grouped queries however many diaries.
    """
    counts: dict[uuid.UUID, int] = dict.fromkeys(diary_ids, 0)
    if not diary_ids:
        return counts
    members = await db.execute(
        select(DiaryPermission.diary_id, func.count())
        .where(DiaryPermission.diary_id.in_(diary_ids))
        .group_by(DiaryPermission.diary_id)
    )
    pending = (
        select(Invitation.diary_id, func.count())
        .where(
            Invitation.diary_id.in_(diary_ids),
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > func.now(),
        )
        .group_by(Invitation.diary_id)
    )
    if exclude_invitation_id is not None:
        pending = pending.where(Invitation.id != exclude_invitation_id)
    for diary_id, n in [*members.all(), *(await db.execute(pending)).all()]:
        counts[diary_id] += n
    return counts


async def people_used(
    db: AsyncSession, diary_id: uuid.UUID, *, exclude_invitation_id: uuid.UUID | None = None
) -> int:
    """Members plus pending (unaccepted, unexpired) invitations."""
    counts = await people_counts(db, [diary_id], exclude_invitation_id=exclude_invitation_id)
    return counts[diary_id]


async def lock_diary_sharing(db: AsyncSession, diary: Diary) -> None:
    """Take the per-diary sharing lock, held to the end of the transaction.

    Re-entrant within a session, so callers may take it again.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('share:' || :diary_id))"),
        {"diary_id": str(diary.id)},
    )


async def ensure_room_for_person(
    db: AsyncSession,
    diary: Diary,
    owner_tier: str,
    *,
    exclude_invitation_id: uuid.UUID | None = None,
) -> None:
    """Raise a tier_limit 403 when the diary is at its people limit.

    Takes a per-diary advisory lock (held to the end of the transaction) so two
    concurrent invites cannot both pass the check.
    """
    await lock_diary_sharing(db, diary)
    limit = limits_for(owner_tier).people_per_diary
    if limit is None:
        return
    used = await people_used(db, diary.id, exclude_invitation_id=exclude_invitation_id)
    if used >= limit:
        raise tier_limit_error(source="people", limit=limit, current=used, needed=used + 1)


def ensure_editor_allowed(owner_tier: str) -> None:
    if not limits_for(owner_tier).editors:
        raise tier_limit_error(source="editor", limit=0, current=0, needed=1)
