"""Whether a Google connection can be used to read the calendar."""

from __future__ import annotations

from typing import Literal

CalendarState = Literal["connected", "lost", "none"]


def calendar_state(oauth_token) -> CalendarState:
    """'none': never connected. 'lost': connected, but revoked or without calendar access."""
    if oauth_token is None:
        return "none"
    if oauth_token.revoked_at is None and "calendar.readonly" in (
        oauth_token.scopes_granted or []
    ):
        return "connected"
    return "lost"
