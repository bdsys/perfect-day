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
