"""Unit tests: Google id_token payload extraction from integrations router."""

from __future__ import annotations

import base64
import json


def _make_id_token(payload: dict) -> str:
    """Build a fake unsigned id_token (header.payload.sig) for testing."""

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64url(b'{"alg":"RS256","typ":"JWT"}')
    body = b64url(json.dumps(payload).encode())
    return f"{header}.{body}.fakesig"


class TestDecodeIdToken:
    def test_extracts_email_and_name(self):
        from app.routers.v1.integrations import _decode_id_token

        token = _make_id_token(
            {"email": "alice@example.com", "name": "Alice Smith", "sub": "12345"}
        )
        result = _decode_id_token(token)
        assert result["email"] == "alice@example.com"
        assert result["name"] == "Alice Smith"

    def test_missing_name_returns_none(self):
        from app.routers.v1.integrations import _decode_id_token

        token = _make_id_token({"email": "bob@example.com", "sub": "99"})
        result = _decode_id_token(token)
        assert result["email"] == "bob@example.com"
        assert result["name"] is None

    def test_missing_email_returns_none(self):
        from app.routers.v1.integrations import _decode_id_token

        token = _make_id_token({"name": "Carol", "sub": "88"})
        result = _decode_id_token(token)
        assert result["email"] is None
        assert result["name"] == "Carol"

    def test_none_input_returns_nones(self):
        from app.routers.v1.integrations import _decode_id_token

        result = _decode_id_token(None)
        assert result["email"] is None
        assert result["name"] is None

    def test_empty_string_returns_nones(self):
        from app.routers.v1.integrations import _decode_id_token

        result = _decode_id_token("")
        assert result["email"] is None
        assert result["name"] is None

    def test_malformed_token_returns_nones(self):
        from app.routers.v1.integrations import _decode_id_token

        result = _decode_id_token("not.a.valid.jwt.at.all")
        assert result["email"] is None
        assert result["name"] is None

    def test_payload_not_valid_json_returns_nones(self):
        from app.routers.v1.integrations import _decode_id_token

        bad_payload = base64.urlsafe_b64encode(b"{{not-json}}").rstrip(b"=").decode()
        token = f"header.{bad_payload}.sig"
        result = _decode_id_token(token)
        assert result["email"] is None
        assert result["name"] is None
