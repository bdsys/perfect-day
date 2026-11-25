from __future__ import annotations

import logging
import re

# Link-only pages are reached by a token in the path: /v1/public/{token}/...
# Anyone holding the token can use it for months, so it must never reach a log.
# Diary-invite tokens (/v1/invitations/{token}) are single-use and stay logged.
_PUBLIC_TOKEN = re.compile(r"^(/v1/public/)[^/?]+(?:\?.*)?")


def redact_path(path: str) -> str:
    return _PUBLIC_TOKEN.sub(lambda m: m.group(1) + "[redacted]", path, count=1)


class PublicTokenAccessFilter(logging.Filter):
    """Rewrites the request path in uvicorn's access log record (args[2])."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > 2 and isinstance(args[2], str):
            record.args = (*args[:2], redact_path(args[2]), *args[3:])
        return True
