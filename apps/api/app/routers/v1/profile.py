"""Save the profile screen's choices: temperature unit and preset avatar icon."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from app.core.auth import get_current_user
from app.models import AVATAR_ICONS, User
from app.services.profile import UserProfile, user_profile

router = APIRouter(prefix="/auth", tags=["profile"])


class ProfilePatch(BaseModel):
    model_config = {"extra": "forbid"}

    temperature_unit: Literal["F", "C"] | None = None
    # Absent: no change. Explicit null: use my initial.
    avatar_icon: str | None = None

    @field_validator("avatar_icon")
    @classmethod
    def _known_icon(cls, v: str | None) -> str | None:
        if v is not None and v not in AVATAR_ICONS:
            raise ValueError("unknown avatar icon")
        return v

    @field_validator("temperature_unit", mode="before")
    @classmethod
    def _unit_not_null(cls, v: object) -> object:
        if v is None:
            raise ValueError("temperature_unit cannot be null")
        return v


@router.patch("/me", response_model=UserProfile)
async def patch_me(body: ProfilePatch, user: User = Depends(get_current_user)) -> UserProfile:
    fields = body.model_fields_set
    if "temperature_unit" in fields and body.temperature_unit is not None:
        user.temperature_unit = body.temperature_unit
    if "avatar_icon" in fields:
        if user.avatar_s3_key is not None:
            # Task 5: delete the stored object here (delete_avatar_object(user.avatar_s3_key)).
            user.avatar_s3_key = None
            user.avatar_dek_ciphertext = None
        user.avatar_icon = body.avatar_icon
    return user_profile(user)
