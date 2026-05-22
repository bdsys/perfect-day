from __future__ import annotations

import uuid

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=scope.get("path", ""))

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders

                headers_obj = MutableHeaders(scope=message)
                headers_obj.append("X-Request-ID", request_id)
            await send(message)

        await self.app(scope, receive, send_with_header)
