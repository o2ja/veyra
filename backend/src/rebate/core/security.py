"""Cryptographic primitives (SECURITY.md §1): opaque tokens, keyed hashes, Argon2id, AES-256-GCM.

No policy here (what counts as a valid password, how long tokens live): that lives in the identity
module. Nothing in this module logs.
"""

import hashlib
import hmac
import os
import secrets
import unicodedata
from dataclasses import dataclass

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rebate.core.config import Settings, parse_encryption_keys

TOKEN_BYTES = 32  # 256-bit opaque tokens


def new_token() -> str:
    """URL-safe random token for sessions, links and CSRF. Only its SHA-256 is ever stored."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def keyed_hash(secret_key: str, purpose: str, value: str) -> str:
    """HMAC-SHA256 so values such as unknown emails can be counted without being stored or logged."""
    return hmac.new(secret_key.encode(), f"{purpose}\x00{value}".encode(), hashlib.sha256).hexdigest()


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    needs_rehash: bool = False


class Passwords:
    """Argon2id with configured parameters; hashes NFKC-normalised passwords."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        # Verified against when no real hash exists, so unknown accounts cost the same time.
        self._dummy = self._hasher.hash(secrets.token_hex(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(normalize_password(password))

    def verify(self, stored_hash: str | None, password: str) -> VerifyResult:
        target = stored_hash or self._dummy
        try:
            self._hasher.verify(target, normalize_password(password))
        except VerifyMismatchError, VerificationError, InvalidHashError:
            return VerifyResult(ok=False)
        if stored_hash is None:
            return VerifyResult(ok=False)
        return VerifyResult(ok=True, needs_rehash=self._hasher.check_needs_rehash(stored_hash))


class SecretBox:
    """AES-256-GCM with key ids (``ENCRYPTION_KEYS``); the first key encrypts, any listed key decrypts.

    Layout: ``kid`` ASCII, ``:``, 12-byte nonce, ciphertext+tag. ``aad`` binds the ciphertext to its
    owner (the user id), so a value copied to another row does not decrypt.
    """

    def __init__(self, raw_keys: str) -> None:
        self._keys = parse_encryption_keys(raw_keys)
        self._active = next(iter(self._keys))

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self._active]).encrypt(nonce, plaintext, aad)
        return self._active.encode() + b":" + nonce + ct

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        kid, sep, rest = blob.partition(b":")
        key = self._keys.get(kid.decode("ascii", "replace")) if sep else None
        if key is None or len(rest) < 12 + 16:
            raise ValueError("ciphertext has an unknown key id or is truncated")
        return AESGCM(key).decrypt(rest[:12], rest[12:], aad)
