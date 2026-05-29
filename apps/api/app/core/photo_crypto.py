"""Chunked AES-256-GCM encryption for photos.

Pure-Python, synchronous, no DB or S3 access.

File layout (binary):
    MAGIC (4 bytes) || chunk_count (uint32) || chunk_size (uint32) || plaintext_total (uint64)
    [ chunk_length (uint32) || ciphertext+tag ] * chunk_count

Wrapped-DEK layout:
    version (1 byte) || nonce (12 bytes) || ciphertext+tag (32 + 16 bytes)
"""

from __future__ import annotations

import secrets
import struct
import uuid
from collections.abc import Callable, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_VERSION_CURRENT = 0x01
CHUNK_SIZE = 1024 * 1024  # 1 MiB plaintext per chunk
MAGIC = b"PD01"
HEADER_FORMAT = "!4sIIQ"  # magic(4s), chunk_count(I), chunk_size(I), plaintext_total(Q)

_HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_master_secret() -> bytes:
    """Hex-decode the master_secret setting into 32 raw bytes."""
    return bytes.fromhex(get_settings().master_secret)


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def derive_kek(user_id: uuid.UUID) -> bytes:
    """Derive a 32-byte Key Encryption Key for *user_id* from the master secret."""
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=user_id.bytes,
        info=b"photo-kek/v1",
    ).derive(_get_master_secret())


def generate_dek() -> bytes:
    """Generate a fresh 32-byte Data Encryption Key."""
    return secrets.token_bytes(32)


# ---------------------------------------------------------------------------
# DEK wrap / unwrap
# ---------------------------------------------------------------------------


def wrap_dek(dek: bytes, user_id: uuid.UUID) -> bytes:
    """Encrypt *dek* under KEK(user_id).

    Returns: version(1) || nonce(12) || ciphertext+tag(48)
    """
    kek = derive_kek(user_id)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(kek).encrypt(nonce, dek, None)
    return bytes([KEY_VERSION_CURRENT]) + nonce + ct


def unwrap_dek(wrapped: bytes, user_id: uuid.UUID) -> bytes:
    """Decrypt a wrapped DEK.

    Raises ValueError for unknown version bytes.
    Raises cryptography.hazmat.primitives.ciphers.aead.InvalidTag on tamper.
    """
    version = wrapped[0]
    if version != KEY_VERSION_CURRENT:
        raise ValueError("unknown_key_version")
    nonce = wrapped[1:13]
    ct = wrapped[13:]
    kek = derive_kek(user_id)
    return AESGCM(kek).decrypt(nonce, ct, None)


# ---------------------------------------------------------------------------
# Chunk nonce derivation
# ---------------------------------------------------------------------------


def derive_chunk_nonce(dek: bytes, chunk_index: int) -> bytes:
    """Derive a 12-byte nonce for *chunk_index* from *dek*."""
    return HKDF(
        algorithm=SHA256(),
        length=12,
        salt=chunk_index.to_bytes(8, "big"),
        info=b"chunk-nonce/v1",
    ).derive(dek)


# ---------------------------------------------------------------------------
# Eager encrypt / decrypt (loads full payload into memory)
# ---------------------------------------------------------------------------


def encrypt_stream(plaintext: bytes, dek: bytes) -> bytes:
    """Encrypt *plaintext* into a chunked AES-256-GCM blob.

    Returns the full binary blob (header + chunk records).
    A zero-length input produces a valid header with chunk_count=0 and no records.
    """
    chunks = [plaintext[i : i + CHUNK_SIZE] for i in range(0, len(plaintext), CHUNK_SIZE)]
    chunk_count = len(chunks)

    header = struct.pack(HEADER_FORMAT, MAGIC, chunk_count, CHUNK_SIZE, len(plaintext))
    records: list[bytes] = []
    for i, chunk in enumerate(chunks):
        nonce = derive_chunk_nonce(dek, i)
        ct = AESGCM(dek).encrypt(nonce, chunk, None)
        records.append(struct.pack("!I", len(ct)) + ct)

    return header + b"".join(records)


def decrypt_stream(ciphertext: bytes, dek: bytes) -> bytes:
    """Decrypt a blob produced by *encrypt_stream*.

    Raises ValueError if magic bytes do not match.
    Raises InvalidTag on any tampered chunk.
    """
    offset = 0
    magic, chunk_count, _chunk_size, _plaintext_total = struct.unpack_from(
        HEADER_FORMAT, ciphertext, offset
    )
    if magic != MAGIC:
        raise ValueError(f"invalid magic: {magic!r}")
    offset += _HEADER_SIZE

    parts: list[bytes] = []
    for i in range(chunk_count):
        (ct_len,) = struct.unpack_from("!I", ciphertext, offset)
        offset += 4
        ct = ciphertext[offset : offset + ct_len]
        offset += ct_len
        nonce = derive_chunk_nonce(dek, i)
        parts.append(AESGCM(dek).decrypt(nonce, ct, None))

    return b"".join(parts)


# ---------------------------------------------------------------------------
# Streaming decrypt (memory-bounded)
# ---------------------------------------------------------------------------


def iter_decrypt_stream(reader: Callable[[int], bytes], dek: bytes) -> Iterator[bytes]:
    """Memory-bounded streaming decrypt.

    *reader* must return exactly N bytes when called as ``reader(N)``.

    Yields decrypted plaintext chunks one at a time.
    Raises ValueError on bad magic; raises InvalidTag on tamper.
    """
    header_data = reader(_HEADER_SIZE)
    magic, chunk_count, _chunk_size, _plaintext_total = struct.unpack(HEADER_FORMAT, header_data)
    if magic != MAGIC:
        raise ValueError(f"invalid magic: {magic!r}")

    for i in range(chunk_count):
        (ct_len,) = struct.unpack("!I", reader(4))
        ct = reader(ct_len)
        nonce = derive_chunk_nonce(dek, i)
        yield AESGCM(dek).decrypt(nonce, ct, None)
