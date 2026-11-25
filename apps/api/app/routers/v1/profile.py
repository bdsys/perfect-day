"""Save the profile screen's choices: temperature unit, preset icon, profile photo."""

from __future__ import annotations

import asyncio
from typing import Literal

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import AVATAR_ICONS, User
from app.routers.v1.photos import read_capped_body
from app.services.avatar import delete_avatar_object, load_avatar, make_avatar_jpeg, store_avatar
from app.services.photo_ingest import UnsupportedPhoto
from app.services.profile import UserProfile, user_profile

router = APIRouter(prefix="/auth", tags=["profile"])


class ProfilePatch(BaseModel):
    model_config = {"extra": "forbid"}

    temperature_unit: Literal["F", "C"] | None = None
    # The name a shared wish list shows ("shared by"). Cannot be blank or null.
    display_name: str | None = None
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

    @field_validator("display_name")
    @classmethod
    def _name_not_blank(cls, v: str | None) -> str:
        name = (v or "").strip()
        if not name or len(name) > 100:
            raise ValueError("display_name must be 1 to 100 characters")
        return name


async def _lock(db: AsyncSession, user: User) -> None:
    """Re-read the user row and lock it until commit, so two avatar changes at once
    cannot both miss the other's object and leave it behind in storage."""
    await db.refresh(user, with_for_update=True)


async def _commit_then_delete(db: AsyncSession, old_key: str | None) -> None:
    """Save first, then drop the replaced object: a failed delete never loses the change."""
    await db.commit()
    if old_key is not None:
        await asyncio.get_running_loop().run_in_executor(None, delete_avatar_object, old_key)


@router.patch("/me", response_model=UserProfile)
async def patch_me(
    body: ProfilePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UserProfile:
    fields = body.model_fields_set
    old_key: str | None = None
    if "avatar_icon" in fields:
        await _lock(db, user)
        old_key = user.avatar_s3_key
        user.avatar_s3_key = None
        user.avatar_dek_ciphertext = None
        user.avatar_icon = body.avatar_icon
    if "temperature_unit" in fields and body.temperature_unit is not None:
        user.temperature_unit = body.temperature_unit
    if "display_name" in fields and body.display_name is not None:
        user.display_name = body.display_name
    await _commit_then_delete(db, old_key)
    return user_profile(user)


@router.put("/me/avatar", response_model=UserProfile)
async def put_avatar(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UserProfile:
    """Set the profile photo from the raw request body (same types and cap as photos)."""
    data = await read_capped_body(request)
    loop = asyncio.get_running_loop()
    try:
        jpeg = await loop.run_in_executor(None, make_avatar_jpeg, data)
    except UnsupportedPhoto:
        raise HTTPException(status_code=415, detail="unsupported_mime") from None
    key, wrapped = await loop.run_in_executor(None, store_avatar, user.id, jpeg)
    try:
        await _lock(db, user)
        old_key = user.avatar_s3_key
        user.avatar_icon = None
        user.avatar_s3_key = key
        user.avatar_dek_ciphertext = wrapped
        await db.commit()
    except Exception:
        await loop.run_in_executor(None, delete_avatar_object, key)  # no orphan
        raise
    if old_key is not None:
        await loop.run_in_executor(None, delete_avatar_object, old_key)
    return user_profile(user)


@router.get("/me/avatar")
async def get_avatar(user: User = Depends(get_current_user)) -> Response:
    """The owner's profile photo. Only ever their own: the key comes from their row."""
    if user.avatar_s3_key is None or user.avatar_dek_ciphertext is None:
        raise HTTPException(status_code=404, detail="not_found")
    key, wrapped, user_id = user.avatar_s3_key, user.avatar_dek_ciphertext, user.id
    try:
        jpeg = await asyncio.get_running_loop().run_in_executor(
            None, lambda: load_avatar(key, wrapped, user_id)
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise HTTPException(status_code=404, detail="not_found") from None
        raise
    return Response(
        content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"}
    )


@router.delete("/me/avatar", response_model=UserProfile)
async def delete_avatar(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> UserProfile:
    """Remove the profile photo (or icon) and go back to the initial."""
    await _lock(db, user)
    old_key = user.avatar_s3_key
    user.avatar_s3_key = None
    user.avatar_dek_ciphertext = None
    user.avatar_icon = None
    await _commit_then_delete(db, old_key)
    return user_profile(user)
