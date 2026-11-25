from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import structlog
from structlog.testing import capture_logs

from app.core.errors import unhandled_exception_handler
from app.core.redact import PublicTokenAccessFilter, redact_path
from app.middleware.request_id import RequestIDMiddleware


def test_redact_public_path():
    assert redact_path("/v1/public/AbC-_9/claims/1") == "/v1/public/[redacted]/claims/1"


def test_redact_bare_token():
    assert redact_path("/v1/public/AbC") == "/v1/public/[redacted]"


def test_redact_query_after_token_goes_too():
    assert redact_path("/v1/public/SECRET?x=1") == "/v1/public/[redacted]"


def test_redact_leaves_invites_and_others():
    assert redact_path("/v1/invitations/tok") == "/v1/invitations/tok"
    assert redact_path("/v1/diaries/1") == "/v1/diaries/1"


def test_access_filter_rewrites_uvicorn_args():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/v1/public/SECRET?x=1", "1.1", 200),
        None,
    )
    assert PublicTokenAccessFilter().filter(record) is True
    assert all("SECRET" not in str(a) for a in record.args)
    assert "SECRET" not in record.getMessage()
    assert "/v1/public/[redacted]" in record.getMessage()


@pytest.mark.asyncio
async def test_request_id_binds_redacted_path():
    seen = {}

    async def app(scope, receive, send):
        seen.update(structlog.contextvars.get_contextvars())

    scope = {"type": "http", "path": "/v1/public/SECRET", "headers": []}
    await RequestIDMiddleware(app)(scope, None, None)
    assert seen["path"] == "/v1/public/[redacted]"
    structlog.contextvars.clear_contextvars()


@pytest.mark.asyncio
async def test_unhandled_exception_logs_redacted_path():
    request = SimpleNamespace(url=SimpleNamespace(path="/v1/public/SECRET"))
    with capture_logs() as logs:
        await unhandled_exception_handler(request, RuntimeError("boom"))  # type: ignore[arg-type]
    assert logs
    assert all("SECRET" not in str(v) for entry in logs for v in entry.values())
