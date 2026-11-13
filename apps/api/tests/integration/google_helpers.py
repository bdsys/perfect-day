"""Shared by the Google connect tests."""

from __future__ import annotations

import hashlib
import json

CONNECT_COOKIE = "pd_google_connect"


def connect_cookie_for(state: str) -> dict[str, str]:
    """The Cookie header the starting browser would send to the callback.

    The cookie is always Secure, so httpx will not send it over http in tests;
    the header is set by hand instead.
    """
    nonce = json.loads(state.rsplit(".", 1)[0])["nonce"]
    return {"Cookie": f"{CONNECT_COOKIE}={hashlib.sha256(nonce.encode()).hexdigest()}"}
