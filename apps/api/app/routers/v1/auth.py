from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_current_user_within_grace
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.middleware.rate_limit import auth_limiter
from app.models import NotificationPreferences, RefreshToken, SocialIdentity, User

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
GRACE_SECONDS = 30  # reuse grace window for rotation


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginRequest(BaseModel):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _issue_tokens(
    user: User,
    response: Response,
    db: AsyncSession,
    device_hint: str | None = None,
) -> TokenResponse:
    settings = get_settings()
    access = create_access_token(user.id, is_admin=user.is_admin)
    raw, token_hash = generate_refresh_token()
    family_id = uuid.uuid4()

    rt = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        family_id=family_id,
        device_hint=device_hint,
        expires_at=datetime.now(tz=UTC) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(rt)
    await db.flush()

    _set_refresh_cookie(response, raw, settings.refresh_token_expire_days)
    return TokenResponse(access_token=access)


def _set_refresh_cookie(response: Response, raw_token: str, expire_days: int) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_token,
        httponly=True,
        samesite="strict",
        secure=True,
        max_age=expire_days * 86400,
        path="/v1/auth",
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@auth_limiter.limit("10/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_taken")

    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    await db.flush()

    # Create default notification preferences
    db.add(NotificationPreferences(user_id=user.id))

    return await _issue_tokens(
        user, response, db, device_hint=request.headers.get("User-Agent", "")[:200]
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
@auth_limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    if (
        user is None
        or user.password_hash is None
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    if user.deleted_at is not None or user.hard_delete_after is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account_unavailable")

    return await _issue_tokens(
        user, response, db, device_hint=request.headers.get("User-Agent", "")[:200]
    )


# ---------------------------------------------------------------------------
# Google social login
# ---------------------------------------------------------------------------


@router.post("/social/google", response_model=TokenResponse)
async def social_google(
    body: GoogleLoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    try:
        id_info = google_id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
            settings.google_client_id,
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_google_token")

    provider_user_id = id_info["sub"]
    email = id_info.get("email", "").lower()
    email_verified = id_info.get("email_verified", False)

    # Check existing social identity
    result = await db.execute(
        select(SocialIdentity).where(
            SocialIdentity.provider == "google",
            SocialIdentity.provider_user_id == provider_user_id,
        )
    )
    identity = result.scalar_one_or_none()

    if identity is not None:
        user_result = await db.execute(select(User).where(User.id == identity.user_id))
        user = user_result.scalar_one_or_none()
        if user is None or user.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="account_unavailable"
            )
        return await _issue_tokens(
            user, response, db, device_hint=request.headers.get("User-Agent", "")[:200]
        )

    # Check if email already exists on a different account → require explicit link
    if email:
        existing_result = await db.execute(select(User).where(User.email == email))
        existing_user = existing_result.scalar_one_or_none()
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="link_required",
            )

    # New user
    user = User(
        email=email,
        display_name=id_info.get("name"),
        email_verified_at=datetime.now(tz=UTC) if email_verified else None,
    )
    db.add(user)
    await db.flush()

    db.add(
        SocialIdentity(
            user_id=user.id,
            provider="google",
            provider_user_id=provider_user_id,
        )
    )
    db.add(NotificationPreferences(user_id=user.id))

    return await _issue_tokens(
        user, response, db, device_hint=request.headers.get("User-Agent", "")[:200]
    )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Origin check for CSRF defence-in-depth
    origin = request.headers.get("origin", "")
    allowed = {
        "https://diary.perfectday.bdsys.net",
        "https://api.diary.perfectday.bdsys.net",
        "http://localhost:3000",
    }
    if origin and origin not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden_origin")

    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    token_hash = hash_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    rt = result.scalar_one_or_none()

    if rt is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    now = datetime.now(tz=UTC)

    # Expired
    if rt.expires_at.replace(tzinfo=UTC) < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")

    # Revoked — check for theft signal or grace window
    if rt.revoked_at is not None:
        revoked_age = (now - rt.revoked_at.replace(tzinfo=UTC)).total_seconds()
        if revoked_age <= GRACE_SECONDS:
            # Within grace window: return the successor token (the one with same family, newer)
            # Find the newest non-revoked token in this family
            successor_result = await db.execute(
                select(RefreshToken).where(
                    RefreshToken.family_id == rt.family_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
            successor = successor_result.scalar_one_or_none()
            if successor is not None:
                user_result = await db.execute(select(User).where(User.id == rt.user_id))
                user = user_result.scalar_one_or_none()
                if user is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
                    )
                access = create_access_token(user.id, is_admin=user.is_admin)
                return TokenResponse(access_token=access)

        # Outside grace window — revoke entire family (theft signal)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == rt.family_id)
            .values(revoked_at=now)
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_reuse_detected")

    settings = get_settings()
    # Rotate: revoke current, issue new
    rt.revoked_at = now
    user_result = await db.execute(select(User).where(User.id == rt.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account_unavailable")

    access = create_access_token(user.id, is_admin=user.is_admin)
    raw, new_hash = generate_refresh_token()
    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=new_hash,
        family_id=rt.family_id,
        device_hint=rt.device_hint,
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(new_rt)

    _set_refresh_cookie(response, raw, settings.refresh_token_expire_days)
    return TokenResponse(access_token=access)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> None:
    if refresh_token:
        token_hash = hash_token(refresh_token)
        result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        rt = result.scalar_one_or_none()
        if rt is not None and rt.revoked_at is None:
            rt.revoked_at = datetime.now(tz=UTC)
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(tz=UTC))
    )
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


class UserProfile(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    subscription_tier: str
    is_admin: bool
    email_verified_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    now = datetime.now(tz=UTC)
    user.deleted_at = now
    user.hard_delete_after = now + timedelta(days=7)

    # Revoke all tokens immediately
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")


@router.post("/account/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_account(
    user: User = Depends(get_current_user_within_grace),
    db: AsyncSession = Depends(get_db),
) -> None:
    # Re-check: get fresh user since the middleware already blocked deleted users,
    # so if they're here they must still be within grace (hard_delete_after not yet passed)
    result = await db.execute(select(User).where(User.id == user.id))
    fresh_user = result.scalar_one()
    fresh_user.deleted_at = None
    fresh_user.hard_delete_after = None
