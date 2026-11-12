"""Chunked AES-256-GCM encryption for photos.

Pure-Python, synchronous, no DB or S3 access.

File layout (binary):
    MAGIC (4 bytes) || chunk_count (uint32) || chunk_size (uint32) || plaintext_total (uint64)
    [ chunk_length (uint32) || ciphertext+tag ] * chunk_count

Two format versions exist, told apart by MAGIC:

* ``PD02`` (written today). Each blob is encrypted under its own subkey,
  HKDF(DEK, info="photo-key/v2/" + purpose), so the full-size image and its
  thumbnail never share a (key, nonce) pair even though they share a DEK. The
  header and the chunk index are bound into every chunk as associated data, so
  editing ``chunk_count`` or reordering chunks fails authentication.
* ``PD01`` (legacy, read-only). Chunks were encrypted directly under the DEK
  with no associated data. A full image and its thumbnail stored this way reuse
  the same key and nonce for chunk 0; re-encrypt such photos to retire it.

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
MAGIC = b"PD02"
MAGIC_V1 = b"PD01"  # legacy blobs: DEK used directly, no associated data
PURPOSE_FULL = b"full"
PURPOSE_THUMB = b"thumb"
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
# Per-blob subkey and associated data (PD02)
# ---------------------------------------------------------------------------


def derive_blob_key(dek: bytes, purpose: bytes) -> bytes:
    """Derive the 32-byte key that encrypts one blob (*purpose*) of a photo.

    Separate subkeys are what keep the full image and the thumbnail from
    sharing a (key, nonce) pair: chunk nonces depend only on the chunk index.
    """
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=None,
        info=b"photo-key/v2/" + purpose,
    ).derive(dek)


def _chunk_aad(header: bytes, chunk_index: int) -> bytes:
    return header + chunk_index.to_bytes(8, "big")


def _chunk_cipher(magic: bytes, dek: bytes, purpose: bytes) -> tuple[AESGCM, bytes, bool]:
    """Return (cipher, nonce_key, use_aad) for a blob with the given magic."""
    if magic == MAGIC:
        key = derive_blob_key(dek, purpose)
        return AESGCM(key), key, True
    if magic == MAGIC_V1:
        return AESGCM(dek), dek, False
    raise ValueError(f"invalid magic: {magic!r}")


# ---------------------------------------------------------------------------
# Eager encrypt / decrypt (loads full payload into memory)
# ---------------------------------------------------------------------------


def encrypt_stream(plaintext: bytes, dek: bytes, purpose: bytes = PURPOSE_FULL) -> bytes:
    """Encrypt *plaintext* into a chunked AES-256-GCM blob (PD02 format).

    *purpose* names which blob of the photo this is (``PURPOSE_FULL`` or
    ``PURPOSE_THUMB``) and must be passed again to decrypt.

    Returns the full binary blob (header + chunk records).
    A zero-length input produces a valid header with chunk_count=0 and no records.
    """
    chunks = [plaintext[i : i + CHUNK_SIZE] for i in range(0, len(plaintext), CHUNK_SIZE)]
    chunk_count = len(chunks)

    header = struct.pack(HEADER_FORMAT, MAGIC, chunk_count, CHUNK_SIZE, len(plaintext))
    key = derive_blob_key(dek, purpose)
    cipher = AESGCM(key)
    records: list[bytes] = []
    for i, chunk in enumerate(chunks):
        nonce = derive_chunk_nonce(key, i)
        ct = cipher.encrypt(nonce, chunk, _chunk_aad(header, i))
        records.append(struct.pack("!I", len(ct)) + ct)

    return header + b"".join(records)


def decrypt_stream(ciphertext: bytes, dek: bytes, purpose: bytes = PURPOSE_FULL) -> bytes:
    """Decrypt a blob produced by *encrypt_stream* (PD02) or a legacy PD01 blob.

    Raises ValueError if magic bytes do not match.
    Raises InvalidTag on any tampered chunk, a tampered header, or the wrong purpose.
    """
    header = ciphertext[:_HEADER_SIZE]
    magic, chunk_count, _chunk_size, _plaintext_total = struct.unpack(HEADER_FORMAT, header)
    cipher, nonce_key, use_aad = _chunk_cipher(magic, dek, purpose)
    offset = _HEADER_SIZE

    parts: list[bytes] = []
    for i in range(chunk_count):
        (ct_len,) = struct.unpack_from("!I", ciphertext, offset)
        offset += 4
        ct = ciphertext[offset : offset + ct_len]
        offset += ct_len
        nonce = derive_chunk_nonce(nonce_key, i)
        parts.append(cipher.decrypt(nonce, ct, _chunk_aad(header, i) if use_aad else None))

    return b"".join(parts)


# ---------------------------------------------------------------------------
# Streaming decrypt (memory-bounded)
# ---------------------------------------------------------------------------


def iter_decrypt_stream(
    reader: Callable[[int], bytes], dek: bytes, purpose: bytes = PURPOSE_FULL
) -> Iterator[bytes]:
    """Memory-bounded streaming decrypt of a PD02 or legacy PD01 blob.

    *reader* must return exactly N bytes when called as ``reader(N)``.

    Yields decrypted plaintext chunks one at a time.
    Raises ValueError on bad magic; raises InvalidTag on tamper or wrong purpose.
    """
    header = reader(_HEADER_SIZE)
    magic, chunk_count, _chunk_size, _plaintext_total = struct.unpack(HEADER_FORMAT, header)
    cipher, nonce_key, use_aad = _chunk_cipher(magic, dek, purpose)

    for i in range(chunk_count):
        (ct_len,) = struct.unpack("!I", reader(4))
        ct = reader(ct_len)
        nonce = derive_chunk_nonce(nonce_key, i)
        yield cipher.decrypt(nonce, ct, _chunk_aad(header, i) if use_aad else None)
