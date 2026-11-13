"""Google Photos Picker API client (item 14).

https://developers.google.com/photos/picker/reference/rest — a session is
created, the parent picks in Google's own page (``pickerUri``), we poll the
session until ``mediaItemsSet``, list the picked items and download each one
from its ``baseUrl`` with ``=d``.

Never log what this module handles: access tokens, base URLs (they grant
access to the photo for an hour) or photo bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx

from app.services.photos import MAX_BYTES

BASE_URL = "https://photospicker.googleapis.com/v1"
PAGE_SIZE = 100
API_TIMEOUT = httpx.Timeout(20.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_POLL_S = 5.0  # when Google's session omits pollingConfig.pollInterval

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


class GoogleError(Exception):
    """Google answered with something we can't use (e.g. 400, 404)."""


class GoogleAuthError(GoogleError):
    """401/403: the grant is gone or doesn't cover the picker. The parent must reconnect."""


class GoogleUnavailable(GoogleError):
    """5xx, 429 (RESOURCE_EXHAUSTED) or a network failure/timeout: try again later."""


class PhotoTooLarge(Exception):  # noqa: N818 — reads as the outcome, like UnsupportedPhoto
    """The download passed MAX_BYTES."""


@dataclass(frozen=True)
class PickerSession:
    id: str
    picker_uri: str
    items_set: bool
    poll_interval_s: float
    timeout_s: float | None
    expire_time: datetime | None


@dataclass(frozen=True)
class PickedItem:
    id: str
    type: str  # "PHOTO", "VIDEO" or "TYPE_UNSPECIFIED"
    create_time: datetime | None
    base_url: str
    mime_type: str | None
    filename: str | None


def parse_duration(value: str | None) -> float | None:
    """Google's JSON duration ("5s", "1.5s") in seconds; None if absent or unreadable."""
    if not value:
        return None
    m = _DURATION.match(value.strip())
    return float(m.group(1)) if m else None


def _parse_time(value: str | None) -> datetime | None:
    """RFC 3339 timestamp; Google may send up to nine fractional digits."""
    if not value:
        return None
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$", value.strip())
    if not m:
        return None
    base, frac, tz = m.groups()
    text = base + (f".{(frac + '000000')[:6]}" if frac else "") + ("+00:00" if tz == "Z" else tz)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _session(data: dict) -> PickerSession:
    polling = data.get("pollingConfig") or {}
    return PickerSession(
        id=data["id"],
        picker_uri=data.get("pickerUri", ""),
        items_set=bool(data.get("mediaItemsSet", False)),
        poll_interval_s=parse_duration(polling.get("pollInterval")) or DEFAULT_POLL_S,
        timeout_s=parse_duration(polling.get("timeoutIn")),
        expire_time=_parse_time(data.get("expireTime")),
    )


def _item(data: dict) -> PickedItem:
    media = data.get("mediaFile") or {}
    return PickedItem(
        id=data["id"],
        type=data.get("type", "TYPE_UNSPECIFIED"),
        create_time=_parse_time(data.get("createTime")),
        base_url=media.get("baseUrl", ""),
        mime_type=media.get("mimeType"),
        filename=media.get("filename"),
    )


def _check(resp: httpx.Response) -> None:
    code = resp.status_code
    if 200 <= code < 300:
        return
    if code in (401, 403):
        raise GoogleAuthError(f"google photos {code}")
    if code == 429 or code >= 500:
        raise GoogleUnavailable(f"google photos {code}")
    raise GoogleError(f"google photos {code}")


async def _require_https(request: httpx.Request) -> None:
    """Refuse any hop (including a redirect) that is not TLS: these are a child's photos."""
    if request.url.scheme != "https":
        raise GoogleError("download redirected to a non-https url")


class GooglePhotosClient:
    def __init__(self, access_token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._transport = transport

    def _client(
        self, timeout: httpx.Timeout = API_TIMEOUT, *, follow_redirects: bool = False
    ) -> httpx.AsyncClient:
        # httpx drops the Authorization header on a redirect to another origin
        # (httpx/_client.py, Client._redirect_headers), so following is safe.
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=timeout,
            headers=self._headers,
            follow_redirects=follow_redirects,
            event_hooks={"request": [_require_https]} if follow_redirects else None,
        )

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            async with self._client() as client:
                resp = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:  # timeouts, connection failures
            raise GoogleUnavailable(type(exc).__name__) from None
        _check(resp)
        return resp

    async def create_session(self, max_items: int = 50) -> PickerSession:
        resp = await self._request(
            "POST",
            f"{BASE_URL}/sessions",
            json={"pickingConfig": {"maxItemCount": str(max_items)}},
        )
        return _session(resp.json())

    async def get_session(self, id: str) -> PickerSession:  # noqa: A002 — named by the interface
        resp = await self._request("GET", f"{BASE_URL}/sessions/{quote(id, safe='')}")
        return _session(resp.json())

    async def delete_session(self, id: str) -> None:  # noqa: A002 — named by the interface
        await self._request("DELETE", f"{BASE_URL}/sessions/{quote(id, safe='')}")

    async def list_items(self, session_id: str) -> list[PickedItem]:
        items: list[PickedItem] = []
        page_token: str | None = None
        while True:
            params = {"sessionId": session_id, "pageSize": str(PAGE_SIZE)}
            if page_token:
                params["pageToken"] = page_token
            data = (await self._request("GET", f"{BASE_URL}/mediaItems", params=params)).json()
            items.extend(_item(raw) for raw in data.get("mediaItems") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                return items

    async def download(self, item: PickedItem) -> bytes:
        """The original bytes (``baseUrl=d``), stopping as soon as they pass MAX_BYTES."""
        if not item.base_url.startswith("https://"):
            # The bearer token must only ever travel over TLS.
            raise GoogleError("base url is not https")
        buf = bytearray()
        try:
            async with self._client(DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                async with client.stream("GET", f"{item.base_url}=d") as resp:
                    _check(resp)
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                        raise PhotoTooLarge()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            raise PhotoTooLarge()
        except httpx.TransportError as exc:
            raise GoogleUnavailable(type(exc).__name__) from None
        return bytes(buf)
