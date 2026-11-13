"""Google Photos Picker API client. Item 14."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.services.google_photos import (
    BASE_URL,
    GoogleAuthError,
    GooglePhotosClient,
    GoogleUnavailable,
    PhotoTooLarge,
    PickedItem,
    parse_duration,
)

SESSION = {
    "id": "sess-1",
    "pickerUri": "https://photos.google.com/picker/sess-1",
    "pollingConfig": {"pollInterval": "5s", "timeoutIn": "1800s"},
    "expireTime": "2026-11-13T11:00:00.123456789Z",
    "mediaItemsSet": False,
}


def _item(i: int, kind: str = "PHOTO") -> dict:
    return {
        "id": f"item-{i}",
        "createTime": "2026-05-16T10:00:00Z",
        "type": kind,
        "mediaFile": {
            "baseUrl": f"https://lh3.googleusercontent.com/base-{i}",
            "mimeType": "image/jpeg",
            "filename": f"IMG_{i}.jpg",
        },
    }


def _client(handler) -> GooglePhotosClient:
    return GooglePhotosClient("ya29.token", transport=httpx.MockTransport(handler))


async def test_create_session_sends_max_items_and_parses_picker_uri():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=SESSION)

    session = await _client(handler).create_session()

    assert seen["method"] == "POST"
    assert seen["url"] == f"{BASE_URL}/sessions"
    assert seen["auth"] == "Bearer ya29.token"
    assert seen["body"] == {"pickingConfig": {"maxItemCount": "50"}}
    assert session.id == "sess-1"
    assert session.picker_uri == "https://photos.google.com/picker/sess-1"
    assert session.items_set is False
    assert session.poll_interval_s == 5.0
    assert session.timeout_s == 1800.0
    assert session.expire_time == datetime(2026, 11, 13, 11, 0, 0, 123456, tzinfo=UTC)


async def test_duration_strings_parse():
    assert parse_duration("5s") == 5.0
    assert parse_duration("1.5s") == 1.5
    assert parse_duration("0.250s") == 0.25
    assert parse_duration(None) is None
    assert parse_duration("") is None
    assert parse_duration("soon") is None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                **SESSION,
                "mediaItemsSet": True,
                "pollingConfig": {"pollInterval": "1.5s"},
            },
        )

    session = await _client(handler).get_session("sess-1")
    assert session.items_set is True
    assert session.poll_interval_s == 1.5
    assert session.timeout_s is None


async def test_list_items_follows_pages():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        assert request.url.path == "/v1/mediaItems"
        if "pageToken" not in params:
            return httpx.Response(
                200, json={"mediaItems": [_item(1), _item(2, "VIDEO")], "nextPageToken": "p2"}
            )
        return httpx.Response(200, json={"mediaItems": [_item(3)]})

    items = await _client(handler).list_items("sess-1")

    assert [c.get("pageToken") for c in calls] == [None, "p2"]
    assert all(c["sessionId"] == "sess-1" and c["pageSize"] == "100" for c in calls)
    assert [i.id for i in items] == ["item-1", "item-2", "item-3"]
    assert items[1].type == "VIDEO"
    assert items[0] == PickedItem(
        id="item-1",
        type="PHOTO",
        create_time=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        base_url="https://lh3.googleusercontent.com/base-1",
        mime_type="image/jpeg",
        filename="IMG_1.jpg",
    )


async def test_download_uses_d_param_and_bearer():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"jpeg-bytes")

    item = PickedItem("item-1", "PHOTO", None, "https://lh3.googleusercontent.com/b", None, "a.jpg")
    data = await _client(handler).download(item)

    assert data == b"jpeg-bytes"
    assert seen["url"] == "https://lh3.googleusercontent.com/b=d"
    assert seen["auth"] == "Bearer ya29.token"


async def test_download_over_limit_raises(monkeypatch):
    import app.services.google_photos as gp

    monkeypatch.setattr(gp, "MAX_BYTES", 10)

    async def chunks():
        yield b"123456"
        yield b"789012"  # passes 10 bytes here
        raise AssertionError("read past the cap")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())  # no Content-Length

    item = PickedItem("item-1", "PHOTO", None, "https://lh3.googleusercontent.com/b", None, "a.jpg")
    with pytest.raises(PhotoTooLarge):
        await _client(handler).download(item)

    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 11)  # Content-Length 11

    with pytest.raises(PhotoTooLarge):
        await _client(declared).download(item)


@pytest.mark.parametrize("status", [401, 403])
async def test_401_raises_google_auth_error(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"status": "UNAUTHENTICATED"}})

    with pytest.raises(GoogleAuthError):
        await _client(handler).get_session("sess-1")


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_5xx_and_rate_limit_raise_google_unavailable(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(GoogleUnavailable):
        await _client(handler).create_session()


async def test_timeout_raises_google_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GoogleUnavailable):
        await _client(handler).list_items("sess-1")


async def test_delete_session_sends_delete():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    await _client(handler).delete_session("sess-1")
    assert seen == {"method": "DELETE", "url": f"{BASE_URL}/sessions/sess-1"}
