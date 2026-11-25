from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings


def _get_user_id_or_ip(request) -> str:
    """Rate-limit key: authenticated user ID if present, else remote IP."""
    user = getattr(request.state, "user", None)
    if user is not None:
        return str(user.id)
    return get_remote_address(request)


limiter = Limiter(key_func=_get_user_id_or_ip, default_limits=[get_settings().rate_limit_default])
auth_limiter = Limiter(key_func=get_remote_address, default_limits=[get_settings().rate_limit_auth])

# Link-only public API: always keyed by IP (callers are anonymous). key_style="endpoint"
# counts per route (view function), not per URL: with slowapi's default "url" every item's
# claim URL had its own counter, and the URL (with the link token) was the scope slowapi
# logs when a limit is hit. Claim and undo stay separate counters (separate routes).
public_limiter = Limiter(key_func=get_remote_address, key_style="endpoint")
PUBLIC_READ_LIMIT = "60/minute"
PUBLIC_WRITE_LIMIT = "20/hour"
