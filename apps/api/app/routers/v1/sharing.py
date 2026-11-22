"""Owner endpoints for sharing a diary: invitations and members."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import Diary, DiaryPermission, Invitation, User
from app.routers.v1.diaries import _get_diary_or_404
from app.services.read_only import ensure_diary_writable
from app.services.sharing import (
    INVITE_TTL,
    effective_role,
    ensure_editor_allowed,
    ensure_room_for_person,
    lock_diary_sharing,
    new_invite_token,
    normalize_email,
    person_name,
)

router = APIRouter(prefix="/diaries", tags=["sharing"])

ShareRole = Literal["viewer", "editor"]


class InvitationCreate(BaseModel):
    email: EmailStr
    role: ShareRole = "viewer"


class RolePatch(BaseModel):
    role: ShareRole


class InvitationOut(BaseModel):
    id: uuid.UUID
    invited_email: str
    role: str
    expires_at: datetime
    status: Literal["pending", "expired"]
    token: str | None = None


class MemberOut(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str
    role: str
    effective_role: str


def _invitation_out(inv: Invitation, token: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        invited_email=inv.invited_email,
        role=inv.role,
        expires_at=inv.expires_at,
        status="pending" if inv.expires_at > datetime.now(UTC) else "expired",
        token=token,
    )


async def _owner_tier(db: AsyncSession, diary: Diary) -> str:
    tier = (
        await db.execute(select(User.subscription_tier).where(User.id == diary.owner_user_id))
    ).scalar_one_or_none()
    return tier or "free"


async def _ensure_not_member_or_invited(
    db: AsyncSession,
    diary: Diary,
    normalized_email: str,
    *,
    exclude_invitation_id: uuid.UUID | None = None,
) -> None:
    """409 already_member / already_invited. Call under the sharing lock."""
    already_member = (
        await db.execute(
            select(DiaryPermission.id)
            .join(User, User.id == DiaryPermission.user_id)
            .where(
                DiaryPermission.diary_id == diary.id,
                func.lower(User.email) == normalized_email,
            )
        )
    ).first()
    if already_member is not None:
        raise HTTPException(status_code=409, detail="already_member")

    pending = select(Invitation.id).where(
        Invitation.diary_id == diary.id,
        Invitation.invited_email == normalized_email,
        Invitation.accepted_at.is_(None),
        Invitation.expires_at > func.now(),
    )
    if exclude_invitation_id is not None:
        pending = pending.where(Invitation.id != exclude_invitation_id)
    if (await db.execute(pending)).first() is not None:
        raise HTTPException(status_code=409, detail="already_invited")


async def create_invitation(
    db: AsyncSession, diary: Diary, owner: User, email: str, role: str
) -> tuple[Invitation, str]:
    """Check the rules, store the invitation (hash only) and return it with the raw token.

    The one place an invitation is made, so a later email send can hook onto it.
    """
    normalized = normalize_email(email)
    if normalized == normalize_email(owner.email):
        raise HTTPException(status_code=422, detail="cannot_invite_self")

    await lock_diary_sharing(db, diary)
    await _ensure_not_member_or_invited(db, diary, normalized)

    owner_tier = await _owner_tier(db, diary)
    if role == "editor":
        ensure_editor_allowed(owner_tier)
    await ensure_room_for_person(db, diary, owner_tier)

    raw, hashed = new_invite_token()
    inv = Invitation(
        diary_id=diary.id,
        invited_email=normalized,
        role=role,
        token=hashed,
        expires_at=datetime.now(UTC) + INVITE_TTL,
    )
    db.add(inv)
    await db.flush()
    return inv, raw


async def _get_invitation(db: AsyncSession, diary: Diary, invitation_id: uuid.UUID) -> Invitation:
    inv = (
        await db.execute(
            select(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.diary_id == diary.id,
                Invitation.accepted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="not_found")
    return inv


@router.post(
    "/{diary_id}/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED
)
async def create_invitation_endpoint(
    diary_id: uuid.UUID,
    body: InvitationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> InvitationOut:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    await ensure_diary_writable(db, diary)
    inv, raw = await create_invitation(db, diary, user, body.email, body.role)
    return _invitation_out(inv, raw)


@router.get("/{diary_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[InvitationOut]:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    rows = (
        (
            await db.execute(
                select(Invitation)
                .where(Invitation.diary_id == diary.id, Invitation.accepted_at.is_(None))
                .order_by(Invitation.created_at.desc(), Invitation.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_invitation_out(i) for i in rows]


@router.delete("/{diary_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invitation(
    diary_id: uuid.UUID,
    invitation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    inv = await _get_invitation(db, diary, invitation_id)
    await db.delete(inv)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{diary_id}/invitations/{invitation_id}/new-link", response_model=InvitationOut)
async def new_invitation_link(
    diary_id: uuid.UUID,
    invitation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> InvitationOut:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    await ensure_diary_writable(db, diary)
    inv = await _get_invitation(db, diary, invitation_id)
    owner_tier = await _owner_tier(db, diary)
    expired = inv.expires_at <= datetime.now(UTC)
    if expired:
        # An expired invite no longer holds a seat; a new link takes one again.
        await lock_diary_sharing(db, diary)
        await _ensure_not_member_or_invited(
            db, diary, normalize_email(inv.invited_email), exclude_invitation_id=inv.id
        )
    if inv.role == "editor":
        ensure_editor_allowed(owner_tier)
    if expired:
        await ensure_room_for_person(db, diary, owner_tier, exclude_invitation_id=inv.id)
    raw, hashed = new_invite_token()
    inv.token = hashed
    inv.expires_at = datetime.now(UTC) + INVITE_TTL
    await db.flush()
    return _invitation_out(inv, raw)


async def _members(db: AsyncSession, diary: Diary) -> list[MemberOut]:
    owner_tier = await _owner_tier(db, diary)
    rows = (
        await db.execute(
            select(DiaryPermission, User)
            .join(User, User.id == DiaryPermission.user_id)
            .where(DiaryPermission.diary_id == diary.id)
        )
    ).all()
    out = [
        MemberOut(
            user_id=u.id,
            name=person_name(u),
            email=u.email,
            role=p.role,
            effective_role=effective_role(p.role, owner_tier),
        )
        for p, u in rows
    ]
    return sorted(out, key=lambda m: m.name.lower())


@router.get("/{diary_id}/permissions", response_model=list[MemberOut])
async def list_members(
    diary_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[MemberOut]:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    return await _members(db, diary)


async def _get_permission(db: AsyncSession, diary: Diary, user_id: uuid.UUID) -> DiaryPermission:
    perm = (
        await db.execute(
            select(DiaryPermission).where(
                DiaryPermission.diary_id == diary.id, DiaryPermission.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=404, detail="not_found")
    return perm


@router.patch("/{diary_id}/permissions/{user_id}", response_model=MemberOut)
async def change_member_role(
    diary_id: uuid.UUID,
    user_id: uuid.UUID,
    body: RolePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> MemberOut:
    diary, _ = await _get_diary_or_404(diary_id, user, db, require_owner=True)
    perm = await _get_permission(db, diary, user_id)
    owner_tier = await _owner_tier(db, diary)
    if body.role == "editor":
        ensure_editor_allowed(owner_tier)
    perm.role = body.role
    await db.flush()
    member = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return MemberOut(
        user_id=member.id,
        name=person_name(member),
        email=member.email,
        role=perm.role,
        effective_role=effective_role(perm.role, owner_tier),
    )


@router.delete("/{diary_id}/permissions/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    diary_id: uuid.UUID,
    user_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """The owner removes anyone; a member removes only themself (leaving)."""
    diary, role = await _get_diary_or_404(diary_id, user, db)
    if role is not None and user_id != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    perm = await _get_permission(db, diary, user_id)
    await db.delete(perm)
    if diary.handover_to_user_id == user_id:
        diary.handover_to_user_id = None
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
