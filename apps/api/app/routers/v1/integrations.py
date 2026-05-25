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
SCOPE_PHOTOS = "https://www.googleapis.com/auth/photoslibrary.readonly"
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
    db: AsyncSession = Depends(get_db),
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
        )
        for t in tokens
    ]


@router.get("/google/authorize")
async def google_authorize(
    scopes: str = Query("calendar", description="comma-separated: calendar,photos"),
    user: User = Depends(get_current_user),
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
    payload = {"user_id": str(user.id), "nonce": nonce}
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
    return {"url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    scope: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    r = get_redis()

    web_base = settings.cors_origins[0] if settings.cors_origins else "http://localhost:3000"

    if error or not code or not state:
        return RedirectResponse(f"{web_base}/diaries?google=denied")

    payload = _verify_state(state, settings.secret_key)
    nonce = payload.get("nonce", "")

    # Single-use nonce check
    deleted = await r.delete(f"oauth_nonce:{nonce}")
    if not deleted:
        return RedirectResponse(f"{web_base}/diaries?google=denied&reason=nonce_reuse")

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
            return RedirectResponse(
                f"{web_base}/diaries?google=denied&reason=token_exchange_failed"
            )
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
        granted_scopes.append("photoslibrary.readonly")

    expires_at = datetime.now(tz=UTC).replace(microsecond=0)
    from datetime import timedelta

    expires_at = expires_at + timedelta(seconds=expires_in)

    # Upsert oauth_tokens
    existing_result = await db.execute(
        select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.provider == "google")
    )
    existing = existing_result.scalar_one_or_none()

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

    # Determine result query param
    requested_calendar = "calendar.readonly" in granted_scopes
    requested_photos = "photoslibrary.readonly" in granted_scopes

    if granted_scopes:
        if not requested_calendar and not requested_photos:
            return RedirectResponse(f"{web_base}/diaries?google=partial&missing=all")
        elif not requested_photos:
            return RedirectResponse(f"{web_base}/diaries?google=partial&missing=photos")
        elif not requested_calendar:
            return RedirectResponse(f"{web_base}/diaries?google=partial&missing=calendar")
        return RedirectResponse(f"{web_base}/diaries?google=connected")
    return RedirectResponse(f"{web_base}/diaries?google=denied")


@router.delete("/google", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_google(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(OAuthToken).where(OAuthToken.user_id == user.id, OAuthToken.provider == "google")
    )
    token = result.scalar_one_or_none()
    if token is not None:
        token.revoked_at = datetime.now(tz=UTC)
