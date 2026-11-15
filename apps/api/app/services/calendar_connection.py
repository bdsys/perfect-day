"""Whether a Google connection can be used to read the calendar."""

from __future__ import annotations

from typing import Literal

CalendarState = Literal["connected", "lost", "none"]


def calendar_state(oauth_token) -> CalendarState:
    """'none': no connection, or one without calendar access (e.g. photos only).
    'lost': a calendar connection that was revoked. 'connected': usable."""
    if oauth_token is None or "calendar.readonly" not in (oauth_token.scopes_granted or []):
        return "none"
    return "lost" if oauth_token.revoked_at is not None else "connected"
