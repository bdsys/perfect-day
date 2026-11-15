"""Revoke a stored Google grant and forget its tokens."""

from __future__ import annotations

import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_oauth_token
from app.models import OAuthToken

logger = logging.getLogger(__name__)

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


async def revoke_and_forget_google(
    db: AsyncSession, token: OAuthToken, *, user_id: uuid.UUID
) -> None:
    """Revoke the grant at Google, then delete the row whatever Google said.

    Does not commit; the caller owns the transaction.
    """
    try:
        secret = decrypt_oauth_token(
            token.refresh_token_ciphertext or token.access_token_ciphertext
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(GOOGLE_REVOKE_URL, data={"token": secret})
        if resp.status_code != 200:
            logger.warning(
                "Google token revoke failed user_id=%s status=%s", user_id, resp.status_code
            )
    except Exception as exc:
        # Never log the exception text or token: only the user and error type.
        logger.warning(
            "Google token revoke failed user_id=%s error=%s", user_id, type(exc).__name__
        )
    await db.delete(token)
