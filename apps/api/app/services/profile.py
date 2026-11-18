"""The signed-in person's profile: what `/me` returns and how the avatar is described."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models import User


class AvatarPhoto(BaseModel):
    kind: Literal["photo"]


class AvatarIcon(BaseModel):
    kind: Literal["icon"]
    name: str


class AvatarInitial(BaseModel):
    kind: Literal["initial"]


class UserProfile(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    subscription_tier: str
    is_admin: bool
    email_verified_at: datetime | None
    temperature_unit: Literal["F", "C"]
    avatar: AvatarPhoto | AvatarIcon | AvatarInitial
    # The exact moment the account is deleted, while it is pending deletion; else None.
    deletion_scheduled_for: datetime | None = None


def _avatar(user: User) -> AvatarPhoto | AvatarIcon | AvatarInitial:
    if user.avatar_s3_key is not None:
        return AvatarPhoto(kind="photo")
    if user.avatar_icon is not None:
        return AvatarIcon(kind="icon", name=user.avatar_icon)
    return AvatarInitial(kind="initial")


def user_profile(user: User) -> UserProfile:
    """Build the profile. Callers reach this through the grace dependency, which has
    already refused blocked accounts, so a set `deleted_at` here means pending."""
    return UserProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        subscription_tier=user.subscription_tier,
        is_admin=user.is_admin,
        email_verified_at=user.email_verified_at,
        temperature_unit="C" if user.temperature_unit == "C" else "F",
        avatar=_avatar(user),
        deletion_scheduled_for=user.hard_delete_after if user.deleted_at is not None else None,
    )
