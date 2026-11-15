"""Google base URLs and picker session paths never reach the logs (item 14).

httpx logs every request URL at INFO; a base URL grants access to a photo for
an hour, so the API and the worker keep the httpx/httpcore loggers at WARNING.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.services.google_photos import GooglePhotosClient, PickedItem

BASE = "https://lh3.googleusercontent.com/SECRET-BASE"
SESSION_ID = "SECRET-SESSION"


@pytest.fixture
def _reset_http_loggers():
    saved = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    for name in saved:
        logging.getLogger(name).setLevel(logging.NOTSET)
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "lh3.googleusercontent.com":
        return httpx.Response(200, content=b"bytes")
    return httpx.Response(
        200, json={"id": SESSION_ID, "pickerUri": "https://photos.google.com/p"}
    )


async def _exercise_client() -> None:
    client = GooglePhotosClient("ya29.token", transport=httpx.MockTransport(_handler))
    await client.download(PickedItem("i", "PHOTO", None, BASE, "image/jpeg", "a.jpg"))
    await client.get_session(SESSION_ID)


def _assert_no_secrets(caplog) -> None:
    for record in caplog.records:
        message = record.getMessage()
        assert "SECRET-BASE" not in message, message
        assert SESSION_ID not in message, message


async def test_unquieted_httpx_would_log_the_base_url(caplog, _reset_http_loggers):
    """Guards the test itself: without the fix, httpx does log the URL."""
    with caplog.at_level(logging.INFO):
        await _exercise_client()
    assert any("SECRET-BASE" in r.getMessage() for r in caplog.records)


async def test_api_logging_setup_keeps_base_urls_out_of_logs(caplog, _reset_http_loggers):
    from app.core.logging import configure_logging

    configure_logging("test")
    with caplog.at_level(logging.INFO):
        await _exercise_client()
    _assert_no_secrets(caplog)


@pytest.mark.parametrize("hook", ["on_after_setup_logger", "on_after_setup_task_logger"])
async def test_worker_logging_setup_keeps_base_urls_out_of_logs(
    caplog, _reset_http_loggers, hook
):
    import app.workers.celery_app as celery_module

    getattr(celery_module, hook)(logger=logging.getLogger("celery"))
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    with caplog.at_level(logging.INFO):
        await _exercise_client()
    _assert_no_secrets(caplog)

