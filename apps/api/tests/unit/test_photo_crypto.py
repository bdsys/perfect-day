"""Unit tests for app.core.photo_crypto — no DB, no S3."""

from __future__ import annotations

import io
import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Ensure settings are satisfied before any import of photo_crypto.
# conftest.py already sets MASTER_SECRET via os.environ.setdefault, so these
# values are already in the environment by the time this module is collected.
# The _clear_settings_cache autouse fixture (defined in conftest.py) resets
# the lru_cache around each test, which is all we need.
# ---------------------------------------------------------------------------

MASTER_SECRET = "a" * 64  # 32 bytes hex-encoded; matches conftest.py default


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixed_uid() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


def _other_uid() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


# ---------------------------------------------------------------------------
# KEK derivation
# ---------------------------------------------------------------------------


class TestKEKDerivation:
    def test_kek_derivation_stable(self):
        """Same (master_secret, user_id) always produces the same KEK."""
        from app.core.photo_crypto import derive_kek

        uid = _fixed_uid()
        kek1 = derive_kek(uid)
        kek2 = derive_kek(uid)
        assert kek1 == kek2
        assert len(kek1) == 32

    def test_different_user_id_gives_different_kek(self):
        from app.core.photo_crypto import derive_kek

        kek1 = derive_kek(_fixed_uid())
        kek2 = derive_kek(_other_uid())
        assert kek1 != kek2


# ---------------------------------------------------------------------------
# DEK wrap / unwrap
# ---------------------------------------------------------------------------


class TestDEKWrapUnwrap:
    def test_dek_wrap_unwrap_round_trip(self):
        from app.core.photo_crypto import generate_dek, unwrap_dek, wrap_dek

        uid = _fixed_uid()
        dek = generate_dek()
        wrapped = wrap_dek(dek, uid)
        recovered = unwrap_dek(wrapped, uid)
        assert recovered == dek

    def test_dek_unwrap_unknown_version_raises_value_error(self):
        from app.core.photo_crypto import generate_dek, wrap_dek

        uid = _fixed_uid()
        dek = generate_dek()
        wrapped = wrap_dek(dek, uid)

        # Replace the version byte with an unknown version (0xFF).
        bad_wrapped = bytes([0xFF]) + wrapped[1:]

        from app.core.photo_crypto import unwrap_dek

        with pytest.raises(ValueError, match="unknown_key_version"):
            unwrap_dek(bad_wrapped, uid)

    def test_wrap_produces_unique_ciphertext_each_time(self):
        """Two wraps of the same DEK must differ because nonces differ."""
        from app.core.photo_crypto import generate_dek, wrap_dek

        uid = _fixed_uid()
        dek = generate_dek()
        assert wrap_dek(dek, uid) != wrap_dek(dek, uid)

    def test_wrapped_length_is_correct(self):
        """Version(1) + nonce(12) + DEK(32) + tag(16) = 61 bytes."""
        from app.core.photo_crypto import generate_dek, wrap_dek

        uid = _fixed_uid()
        dek = generate_dek()
        wrapped = wrap_dek(dek, uid)
        assert len(wrapped) == 1 + 12 + 32 + 16


# ---------------------------------------------------------------------------
# Chunk nonce derivation
# ---------------------------------------------------------------------------


class TestChunkNonceDerivation:
    def test_chunk_nonce_unique_per_index(self):
        """All 1000 per-chunk nonces for a fixed DEK must be distinct."""
        from app.core.photo_crypto import derive_chunk_nonce, generate_dek

        dek = generate_dek()
        nonces = [derive_chunk_nonce(dek, i) for i in range(1000)]
        assert len(set(nonces)) == 1000

    def test_chunk_nonce_length_is_12(self):
        from app.core.photo_crypto import derive_chunk_nonce, generate_dek

        dek = generate_dek()
        assert len(derive_chunk_nonce(dek, 0)) == 12

    def test_chunk_nonce_stable(self):
        """Same dek + index → same nonce (deterministic)."""
        from app.core.photo_crypto import derive_chunk_nonce, generate_dek

        dek = generate_dek()
        assert derive_chunk_nonce(dek, 5) == derive_chunk_nonce(dek, 5)


# ---------------------------------------------------------------------------
# Encrypt / decrypt round-trip
# ---------------------------------------------------------------------------

CHUNK_SIZE = 1024 * 1024  # must mirror photo_crypto.CHUNK_SIZE


@pytest.mark.parametrize(
    "size",
    [0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 5 * CHUNK_SIZE + 13],
)
class TestEncryptDecryptRoundTrip:
    def test_encrypt_decrypt_round_trip(self, size: int):
        from app.core.photo_crypto import decrypt_stream, encrypt_stream, generate_dek

        plaintext = os.urandom(size)
        dek = generate_dek()
        blob = encrypt_stream(plaintext, dek)
        recovered = decrypt_stream(blob, dek)
        assert recovered == plaintext


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_tampered_chunk_fails(self):
        """Flipping a bit in the middle chunk raises InvalidTag."""
        import struct

        from cryptography.exceptions import InvalidTag

        from app.core.photo_crypto import CHUNK_SIZE as _CS
        from app.core.photo_crypto import (
            HEADER_FORMAT,
            decrypt_stream,
            encrypt_stream,
            generate_dek,
        )

        plaintext = os.urandom(3 * _CS)
        dek = generate_dek()
        blob = bytearray(encrypt_stream(plaintext, dek))

        # Locate the start of the second chunk record (index 1).
        header_size = struct.calcsize(HEADER_FORMAT)
        offset = header_size
        # Skip chunk 0: read its length, then skip length bytes.
        (ct_len_0,) = struct.unpack_from("!I", blob, offset)
        offset += 4 + ct_len_0
        # Now at chunk 1 length field; skip it and flip a byte in the ciphertext.
        (ct_len_1,) = struct.unpack_from("!I", blob, offset)
        ct_start = offset + 4
        # Flip the middle byte of chunk 1's ciphertext.
        mid = ct_start + ct_len_1 // 2
        blob[mid] ^= 0xFF

        with pytest.raises(InvalidTag):
            decrypt_stream(bytes(blob), dek)


# ---------------------------------------------------------------------------
# Streaming decrypt
# ---------------------------------------------------------------------------


class TestStreamingDecrypt:
    def test_streaming_decrypt_matches_eager_decrypt(self):
        """iter_decrypt_stream with a 17-byte-at-a-time reader matches decrypt_stream."""
        from app.core.photo_crypto import (
            decrypt_stream,
            encrypt_stream,
            generate_dek,
            iter_decrypt_stream,
        )

        plaintext = os.urandom(3 * CHUNK_SIZE)
        dek = generate_dek()
        blob = encrypt_stream(plaintext, dek)

        buf = io.BytesIO(blob)

        def reader(n: int) -> bytes:
            # Read in 17-byte sub-chunks to stress-test the reader contract.
            result = bytearray()
            remaining = n
            while remaining > 0:
                chunk = buf.read(min(17, remaining))
                if not chunk:
                    raise EOFError(f"unexpected EOF; wanted {remaining} more bytes")
                result.extend(chunk)
                remaining -= len(chunk)
            return bytes(result)

        streamed = b"".join(iter_decrypt_stream(reader, dek))
        eager = decrypt_stream(blob, dek)

        assert streamed == eager

    def test_streaming_tamper_raises_invalid_tag(self):
        """iter_decrypt_stream also raises InvalidTag on tampered ciphertext."""
        import struct

        from cryptography.exceptions import InvalidTag

        from app.core.photo_crypto import CHUNK_SIZE as _CS
        from app.core.photo_crypto import (
            HEADER_FORMAT,
            encrypt_stream,
            generate_dek,
            iter_decrypt_stream,
        )

        plaintext = os.urandom(2 * _CS)
        dek = generate_dek()
        blob = bytearray(encrypt_stream(plaintext, dek))

        # Flip a byte in the second chunk.
        header_size = struct.calcsize(HEADER_FORMAT)
        offset = header_size
        (ct_len_0,) = struct.unpack_from("!I", blob, offset)
        offset += 4 + ct_len_0
        (ct_len_1,) = struct.unpack_from("!I", blob, offset)
        blob[offset + 4 + ct_len_1 // 2] ^= 0x01

        buf = io.BytesIO(bytes(blob))

        def reader(n: int) -> bytes:
            data = buf.read(n)
            return data

        with pytest.raises(InvalidTag):
            # Consume all chunks to trigger the tampered one.
            list(iter_decrypt_stream(reader, dek))
