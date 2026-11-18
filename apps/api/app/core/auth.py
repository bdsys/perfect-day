from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import User

_bearer = HTTPBearer(auto_error=False)

DeletionState = Literal["active", "pending", "blocked"]


def deletion_state(user: User, now: datetime | None = None) -> DeletionState:
    """Where the account stands in deletion. The one definition every check uses.

    - active: never deleted (both columns null).
    - pending: deleted by its owner, inside the grace window (both set, deadline in the
      future). The owner may log in and restore; everything else answers 403.
    - blocked: anything else. The admin CLI sets deleted_at with no deadline; a
      deadline at or before now means the hourly job may already have run.
    """
    if user.deleted_at is None and user.hard_delete_after is None:
        return "active"
    now = now or datetime.now(tz=UTC)
    if (
        user.deleted_at is not None
        and user.hard_delete_after is not None
        and user.hard_delete_after > now
    ):
        return "pending"
    return "blocked"


def account_unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account_unavailable")


async def _user_from_token(
    credentials: HTTPAuthorizationCredentials | None, db: AsyncSession
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> User:
    user = await _user_from_token(credentials, db)

    state = deletion_state(user)
    if state == "blocked":
        raise account_unavailable()
    if state == "pending":
        # 403, not 401: the web app must not take it for an expired session and
        # loop through refresh. It shows the restore screen instead.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="account_pending_deletion"
        )

    # Cache user on request.state for rate limiter key
    request.state.user = user
    return user


async def get_current_user_within_grace(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> User:
    """Like get_current_user but also lets an account pending deletion through.

    Used only by GET /v1/auth/me and POST /v1/auth/account/restore, so the owner can
    see the deadline and undo the deletion. Blocked accounts (admin CLI, or past the
    deadline) are refused here too.
    """
    user = await _user_from_token(credentials, db)
    if deletion_state(user) == "blocked":
        raise account_unavailable()
    request.state.user = user
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return user


async def require_reauth(
    request: Request,
    user: User = Depends(get_current_user),
) -> None:
    """Check that the caller re-authenticated within 15 min. For destructive admin ops."""
    from app.core.dependencies import get_redis

    key = f"reauth:{user.id}:{request.headers.get('X-Session-ID', '')}"
    if not await get_redis().exists(key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="reauth_required")
