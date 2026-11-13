from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import get_redis
from app.core.security import encrypt_oauth_token
from app.models import OAuthToken, User

router = APIRouter(prefix="/integrations", tags=["integrations"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_PHOTOS = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
PHOTOS_SHORT = "photospicker.mediaitems.readonly"
DEFAULT_RETURN_TO = "/diaries"
SCOPE_OPENID = "openid email profile"

NONCE_TTL = 600  # 10 minutes


# ---------------------------------------------------------------------------
# id_token helpers
# ---------------------------------------------------------------------------


def _decode_id_token(id_token: str | None) -> dict:
    """Extract email and name from a Google id_token JWT payload without signature verification.

    The token comes directly from Google over TLS, so we trust the source.
    Returns {"email": str|None, "name": str|None}; never raises.
    """
    try:
        if not id_token:
            return {"email": None, "name": None}
        parts = id_token.split(".")
        if len(parts) < 2:
            return {"email": None, "name": None}
        # Add padding so base64 decode doesn't fail
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return {"email": payload.get("email"), "name": payload.get("name")}
    except Exception:
        return {"email": None, "name": None}


def _safe_return_to(value: str | None) -> str:
    """A same-site path only; anything else falls back to the diaries page."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or "://" in value
        or any(ord(c) < 32 for c in value)
    ):
        return DEFAULT_RETURN_TO
    return value


def _redirect(web_base: str, return_to: str, **query: str) -> RedirectResponse:
    sep = "&" if "?" in return_to else "?"
    return RedirectResponse(f"{web_base}{return_to}{sep}{urlencode(query)}")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class IntegrationOut(BaseModel):
    provider: str
    scopes_granted: list[str]
    revoked: bool
    expires_at: datetime | None
    google_email: str | None = None
    google_name: str | None = None
    photos: bool = False

    model_config = {"from_attributes": False}


# ---------------------------------------------------------------------------
# State HMAC helpers
# ---------------------------------------------------------------------------


def _sign_state(payload: dict, secret: str) -> str:
    data = json.dumps(payload, sort_keys=True)
    sig = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"


def _verify_state(state: str, secret: str) -> dict:
    try:
        data, sig = state.rsplit(".", 1)
        expected = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("invalid signature")
        return json.loads(data)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_state")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[IntegrationOut]:
    result = await db.execute(select(OAuthToken).where(OAuthToken.user_id == user.id))
    tokens = result.scalars().all()
    return [
        IntegrationOut(
            provider=t.provider,
            scopes_granted=t.scopes_granted,
            revoked=t.revoked_at is not None,
            expires_at=t.expires_at,
            google_email=t.google_email,
            google_name=t.google_name,
            photos=t.revoked_at is None and PHOTOS_SHORT in (t.scopes_granted or []),
        )
        for t in tokens
    ]


@router.get("/google/authorize")
async def google_authorize(
    scopes: str = Query("calendar", description="comma-separated: calendar,photos"),
    return_to: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    settings = get_settings()
    r = get_redis()

    requested = [s.strip() for s in scopes.split(",")]
    scope_parts = [SCOPE_OPENID]
    if "calendar" in requested:
        scope_parts.append(SCOPE_CALENDAR)
    if "photos" in requested:
        scope_parts.append(SCOPE_PHOTOS)

    nonce = secrets.token_urlsafe(32)
    # The callback compares what was asked for with what Google granted.
    asked_for = sorted(s for s in set(requested) if s in ("calendar", "photos"))
    payload = {
        "user_id": str(user.id),
        "nonce": nonce,
        "scopes": asked_for,
        "return_to": _safe_return_to(return_to),
    }
    state = _sign_state(payload, settings.secret_key)

    # Store nonce in Redis (single-use, 10-min TTL)
    await r.setex(f"oauth_nonce:{nonce}", NONCE_TTL, "1")

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scope_parts),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    # Steer Google to the account already connected, so Calendar is not replaced.
    existing = (
        await db.execute(
            select(OAuthToken).where(OAuthToken.user_id == user.id, OAuthToken.provider == "google")
        )
    ).scalar_one_or_none()
    if existing is not None and existing.revoked_at is None and existing.google_email:
        params["login_hint"] = existing.google_email
    return {"url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    scope: str | None = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> RedirectResponse:
    settings = get_settings()
    r = get_redis()

    web_base = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"

    return_to = DEFAULT_RETURN_TO
    if state:
        try:
            return_to = _safe_return_to(_verify_state(state, settings.secret_key).get("return_to"))
        except HTTPException:
            pass  # a bad state is rejected below, once it matters

    if error == "access_denied":
        # The person pressed Cancel on Google's screen.
        return _redirect(web_base, return_to, google="cancelled")
    if error or not code or not state:
        return _redirect(web_base, return_to, google="failed")

    payload = _verify_state(state, settings.secret_key)
    nonce = payload.get("nonce", "")

    # Single-use nonce check
    deleted = await r.delete(f"oauth_nonce:{nonce}")
    if not deleted:
        return _redirect(web_base, return_to, google="failed", reason="nonce_reuse")

    user_id = uuid.UUID(payload["user_id"])

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            return _redirect(web_base, return_to, google="failed", reason="token_exchange_failed")
        token_data = resp.json()

    identity = _decode_id_token(token_data.get("id_token"))
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    granted_scope_str = scope or token_data.get("scope", "")

    granted_scopes: list[str] = []
    if SCOPE_CALENDAR in granted_scope_str:
        granted_scopes.append("calendar.readonly")
    if SCOPE_PHOTOS in granted_scope_str:
        granted_scopes.append(PHOTOS_SHORT)

    # Google shows each permission with its own checkbox. Without calendar
    # access the connection is no use, so nothing is saved: an existing
    # connection, working or lost, is left exactly as it was (C-19).
    asked_for = set(payload.get("scopes", ["calendar"]))
    granted = {"calendar"} if "calendar.readonly" in granted_scopes else set()
    if PHOTOS_SHORT in granted_scopes:
        granted.add("photos")
    missing = asked_for - granted
    if "calendar" in missing:
        return _redirect(web_base, return_to, google="partial", missing="calendar")

    existing_result = await db.execute(
        select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.provider == "google")
    )
    existing = existing_result.scalar_one_or_none()

    # Photos-only connect: nothing to save if the picker permission was not given.
    if "photos" in missing and "calendar" not in asked_for:
        return _redirect(web_base, return_to, google="partial", missing="photos")

    # One Google account per person: a different account never replaces a
    # live connection, so Calendar is never swapped out.
    account_matches = False
    if existing is not None and existing.revoked_at is None:
        stored = (existing.google_email or "").lower()
        # A stored NULL email (older rows) accepts the consenting account and
        # records it below. Otherwise the consenting email must be present
        # and match; a missing one is never treated as a match.
        if stored and stored != (identity["email"] or "").lower():
            return _redirect(
                web_base, return_to, google="wrong_account", expected=existing.google_email or ""
            )
        account_matches = True
    if account_matches:
        granted_scopes = list(dict.fromkeys([*granted_scopes, *(existing.scopes_granted or [])]))

    expires_at = datetime.now(tz=UTC).replace(microsecond=0)
    from datetime import timedelta

    expires_at = expires_at + timedelta(seconds=expires_in)

    if existing is not None:
        existing.access_token_ciphertext = encrypt_oauth_token(access_token)
        if refresh_token:
            existing.refresh_token_ciphertext = encrypt_oauth_token(refresh_token)
        existing.scopes_granted = granted_scopes
        existing.expires_at = expires_at
        existing.revoked_at = None
        existing.google_email = identity["email"]
        existing.google_name = identity["name"]
    else:
        db.add(
            OAuthToken(
                user_id=user_id,
                provider="google",
                access_token_ciphertext=encrypt_oauth_token(access_token),
                refresh_token_ciphertext=encrypt_oauth_token(refresh_token)
                if refresh_token
                else None,
                scopes_granted=granted_scopes,
                expires_at=expires_at,
                google_email=identity["email"],
                google_name=identity["name"],
            )
        )
    await db.commit()

    if "photos" in missing:
        return _redirect(web_base, return_to, google="partial", missing="photos")
    return _redirect(web_base, return_to, google="connected")


@router.delete("/google", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_google(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> None:
    result = await db.execute(
        select(OAuthToken).where(OAuthToken.user_id == user.id, OAuthToken.provider == "google")
    )
    token = result.scalar_one_or_none()
    if token is not None:
        token.revoked_at = datetime.now(tz=UTC)
