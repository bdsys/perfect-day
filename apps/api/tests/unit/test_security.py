"""Unit tests: password hashing, JWT, refresh token, AES-256-GCM OAuth token encryption."""
from __future__ import annotations

import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_and_verify(self):
        from app.core.security import hash_password, verify_password
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed) is True

    def test_wrong_password_rejected(self):
        from app.core.security import hash_password, verify_password
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_empty_password_hashes(self):
        from app.core.security import hash_password, verify_password
        hashed = hash_password("")
        assert verify_password("", hashed) is True

    def test_hashes_are_unique(self):
        from app.core.security import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # argon2 includes salt

    def test_needs_rehash_returns_bool(self):
        from app.core.security import hash_password, needs_rehash
        hashed = hash_password("pw")
        assert isinstance(needs_rehash(hashed), bool)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

class TestJWT:
    def test_roundtrip(self):
        from app.core.security import create_access_token, decode_access_token
        uid = uuid.uuid4()
        token = create_access_token(uid)
        payload = decode_access_token(token)
        assert payload["sub"] == str(uid)
        assert payload["type"] == "access"

    def test_admin_flag(self):
        from app.core.security import create_access_token, decode_access_token
        uid = uuid.uuid4()
        token = create_access_token(uid, is_admin=True)
        payload = decode_access_token(token)
        assert payload["admin"] is True

    def test_tampered_token_rejected(self):
        from jose import JWTError
        from app.core.security import create_access_token, decode_access_token
        uid = uuid.uuid4()
        token = create_access_token(uid)
        tampered = token[:-4] + "xxxx"
        with pytest.raises(JWTError):
            decode_access_token(tampered)

    def test_wrong_key_rejected(self, monkeypatch):
        from jose import JWTError
        from app.core.security import create_access_token
        from app.core import config

        uid = uuid.uuid4()
        token = create_access_token(uid)

        # Patch settings to use a different key for decoding
        import os
        monkeypatch.setenv("SECRET_KEY", "completely-different-key")
        config.get_settings.cache_clear()

        from app.core.security import decode_access_token
        with pytest.raises(JWTError):
            decode_access_token(token)


# ---------------------------------------------------------------------------
# Refresh token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    def test_generate_returns_tuple(self):
        from app.core.security import generate_refresh_token
        raw, hashed = generate_refresh_token()
        assert isinstance(raw, str)
        assert isinstance(hashed, str)
        assert len(raw) > 30

    def test_hash_is_deterministic(self):
        from app.core.security import hash_token
        assert hash_token("abc") == hash_token("abc")

    def test_raw_and_hash_differ(self):
        from app.core.security import generate_refresh_token
        raw, hashed = generate_refresh_token()
        assert raw != hashed

    def test_different_raws_produce_different_hashes(self):
        from app.core.security import generate_refresh_token
        _, h1 = generate_refresh_token()
        _, h2 = generate_refresh_token()
        assert h1 != h2


# ---------------------------------------------------------------------------
# AES-256-GCM OAuth token encryption
# ---------------------------------------------------------------------------

class TestAESGCM:
    def test_encrypt_decrypt_roundtrip(self):
        from app.core.security import encrypt_oauth_token, decrypt_oauth_token
        plain = "ya29.some-google-access-token"
        ct = encrypt_oauth_token(plain)
        assert decrypt_oauth_token(ct) == plain

    def test_each_encrypt_produces_unique_ciphertext(self):
        from app.core.security import encrypt_oauth_token
        ct1 = encrypt_oauth_token("same-token")
        ct2 = encrypt_oauth_token("same-token")
        assert ct1 != ct2  # unique nonce each time

    def test_ciphertext_includes_nonce(self):
        from app.core.security import encrypt_oauth_token
        ct = encrypt_oauth_token("token")
        # First 12 bytes are nonce; rest is ciphertext+tag (>= 16 bytes tag)
        assert len(ct) >= 12 + 16

    def test_tampered_ciphertext_rejected(self):
        from cryptography.exceptions import InvalidTag
        from app.core.security import encrypt_oauth_token, decrypt_oauth_token
        ct = bytearray(encrypt_oauth_token("token"))
        ct[-1] ^= 0xFF  # flip last bit
        with pytest.raises(Exception):  # InvalidTag or similar
            decrypt_oauth_token(bytes(ct))

    def test_empty_string(self):
        from app.core.security import encrypt_oauth_token, decrypt_oauth_token
        assert decrypt_oauth_token(encrypt_oauth_token("")) == ""

    def test_long_token(self):
        from app.core.security import encrypt_oauth_token, decrypt_oauth_token
        long_token = "x" * 4096
        assert decrypt_oauth_token(encrypt_oauth_token(long_token)) == long_token

    def test_unicode_token(self):
        from app.core.security import encrypt_oauth_token, decrypt_oauth_token
        token = "ya29.café-token-üñícode"
        assert decrypt_oauth_token(encrypt_oauth_token(token)) == token
