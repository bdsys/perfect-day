from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PREFIX = "/v1/public/"


class PublicHeadersMiddleware:
    """Keep link-only pages out of search, referrers and caches, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(PUBLIC_PREFIX):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Robots-Tag"] = "noindex, nofollow"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)
