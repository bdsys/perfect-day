"""Google OAuth token refresh with per-(user, provider) advisory lock."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from app.core.config import get_settings
from app.core.security import decrypt_oauth_token, encrypt_oauth_token

log = structlog.get_logger()

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def ensure_fresh_access_token(oauth_token, db) -> str | None:
    """Return a valid plaintext access token, refreshing if needed. Returns None if revoked."""
    if oauth_token.revoked_at is not None:
        return None

    now = datetime.now(tz=timezone.utc)
    if oauth_token.expires_at and oauth_token.expires_at.replace(tzinfo=timezone.utc) > now + timedelta(seconds=60):
        return decrypt_oauth_token(oauth_token.access_token_ciphertext)

    # Needs refresh — take advisory lock to prevent double-refresh (token rotation)
    from app.core.dependencies import get_redis
    r = get_redis()
    lock_key = f"oauth_refresh:{oauth_token.user_id}:{oauth_token.provider}"

    # Try to acquire for up to 35 seconds
    for attempt in range(7):
        acquired = await r.set(lock_key, "1", nx=True, ex=30)
        if acquired:
            break
        await asyncio.sleep(5)
    else:
        # Could not acquire; re-read the token in case another worker refreshed it
        from sqlalchemy import select
        from app.models import OAuthToken
        result = await db.execute(
            select(OAuthToken).where(
                OAuthToken.user_id == oauth_token.user_id,
                OAuthToken.provider == oauth_token.provider,
            )
        )
        fresh = result.scalar_one_or_none()
        if fresh and fresh.revoked_at is None:
            return decrypt_oauth_token(fresh.access_token_ciphertext)
        return None

    try:
        settings = get_settings()
        if not oauth_token.refresh_token_ciphertext:
            log.warning("no_refresh_token", user_id=str(oauth_token.user_id))
            return None

        refresh_token = decrypt_oauth_token(oauth_token.refresh_token_ciphertext)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )

        if resp.status_code != 200:
            log.error("token_refresh_failed", status=resp.status_code, user_id=str(oauth_token.user_id))
            oauth_token.revoked_at = datetime.now(tz=timezone.utc)
            return None

        token_data = resp.json()
        new_access = token_data.get("access_token", "")
        expires_in = token_data.get("expires_in", 3600)

        oauth_token.access_token_ciphertext = encrypt_oauth_token(new_access)
        oauth_token.expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)
        # Google may issue a new refresh token
        if "refresh_token" in token_data:
            oauth_token.refresh_token_ciphertext = encrypt_oauth_token(token_data["refresh_token"])

        return new_access
    finally:
        await r.delete(lock_key)
