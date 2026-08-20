"""Symmetric at-rest encryption for PII columns (Task 23).

Two encryption *modes* are provided, because a single "always randomize
the ciphertext" scheme would silently break `POST /auth/login`
(`backend/app/api/auth.py`), which does
``db.query(Customer).filter(Customer.email == credentials.email).one_or_none()``.
A SQL `=` filter compares the *ciphertext* stored in the column against a
freshly-encrypted lookup value computed from the request body -- if
encryption is randomized (a fresh IV/nonce every call), the same plaintext
email produces a different ciphertext on every call, so `==` would never
match an existing row again.

  * `encrypt_deterministic` / `decrypt_deterministic` -- AES-SIV (RFC 5297),
    via `cryptography`'s `AESSIV`. AES-SIV is a purpose-built *deterministic*
    authenticated-encryption construction: the "nonce" it uses internally
    (the synthetic IV) is derived from the plaintext itself via CMAC, so the
    same plaintext + key always yields the same ciphertext, and -- unlike a
    hand-rolled "reuse a fixed nonce with AES-GCM" scheme -- it is designed
    from the ground up to be safe without a caller-supplied nonce (that is
    the whole point of the "synthetic IV" in its name; ordinary AEAD modes
    like GCM/CTR catastrophically fail, e.g. keystream reuse or forgeries,
    if their nonce is ever repeated). Used ONLY for `Customer.email`, the one
    column actually queried by equality (`app/api/auth.py`, confirmed by a
    full-repo grep -- see Task 23's report). Reused code paths: `POST
    /auth/login`, `app/seed/seed.py`'s idempotency check (which queries
    `Customer` generically, not by email, but still round-trips through the
    same encrypted type).
    TRADEOFF (accepted, standard for searchable/indexed encrypted PII):
    deterministic ciphertext leaks whether two rows share the same
    plaintext (equal ciphertext <=> equal email) to anyone with column
    access, even though the plaintext itself stays hidden. This is
    unavoidable for any encrypted column that must remain a working `=`
    lookup target without a separate blind-index/HMAC column -- adding a
    second index layer is out of scope for this POC per the plan's "no new
    architectural layer" constraint.
  * `encrypt_randomized` / `decrypt_randomized` -- AES-256-GCM with a fresh
    random 96-bit nonce per call (`os.urandom`, never a fixed/derived
    value), prepended to the ciphertext so the paired nonce is always
    available to `decrypt_randomized`. Used for `Customer.full_name` and
    `Customer.phone_number` -- confirmed by the same grep that nothing in
    this codebase (`app/api/*`, `app/seed/*`, `backend/tests/*`) queries
    either column by equality, so there is no need to accept the
    determinism tradeoff for them; standard randomized AEAD is strictly
    stronger (no ciphertext-equality leakage at all) and is used instead.

Key handling: a single `ENCRYPTION_KEY` env var (`app.config.settings`,
base64-encoded, >=32 raw bytes) is the only secret an operator has to
manage. Two independent subkeys -- one per mode above -- are derived from it
via HKDF-SHA256 with distinct `info` labels, rather than using the same raw
key for two different constructions (mixing AES-GCM and AES-SIV under one
literal key is unnecessary key reuse across algorithms; HKDF with distinct
labels is the standard way to safely fan one high-entropy secret out into
several independent-looking subkeys).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

from app.config import settings

# AES-GCM's standard/recommended nonce size (96 bits). Using any other size
# is legal but loses the implicit safety margin the construction is
# designed around, so this is not made configurable.
_GCM_NONCE_LENGTH = 12

_HKDF_INFO_RANDOMIZED = b"telematics-pii-randomized-v1"
_HKDF_INFO_DETERMINISTIC = b"telematics-pii-deterministic-v1"


class EncryptionKeyError(RuntimeError):
    """`ENCRYPTION_KEY` is missing, malformed, or too short to be a real key."""


def _master_key_bytes() -> bytes:
    """Decode+validate `settings.encryption_key`. Never cached at module
    scope so tests can freely monkeypatch `settings.encryption_key` between
    calls (mirrors `app.auth.security` reading `settings.jwt_secret` fresh
    on every call rather than snapshotting it at import time)."""
    raw = settings.encryption_key
    if not raw:
        raise EncryptionKeyError("ENCRYPTION_KEY is not set")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # binascii.Error / ValueError on bad base64
        raise EncryptionKeyError("ENCRYPTION_KEY must be valid base64") from exc
    if len(key) < 32:
        raise EncryptionKeyError(
            "ENCRYPTION_KEY must decode to at least 32 bytes (256 bits) of entropy"
        )
    return key


def _derive_key(info: bytes, length: int) -> bytes:
    """HKDF-SHA256-derive a `length`-byte subkey from the master key, scoped
    by `info` so the randomized and deterministic modes never share key
    material even though both trace back to the same `ENCRYPTION_KEY`."""
    hkdf = HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info)
    return hkdf.derive(_master_key_bytes())


def _randomized_key() -> bytes:
    return _derive_key(_HKDF_INFO_RANDOMIZED, 32)  # AES-256-GCM key


def _deterministic_key() -> bytes:
    # AES-SIV keys are double-length (two internal AES-256 subkeys), per
    # RFC 5297 -- 64 bytes here, not 32.
    return _derive_key(_HKDF_INFO_DETERMINISTIC, 64)


# ---------------------------------------------------------------------------
# Randomized mode (AES-256-GCM, fresh random nonce every call)
# ---------------------------------------------------------------------------


def encrypt_randomized(plaintext: str) -> bytes:
    """Encrypt `plaintext` with AES-256-GCM under a freshly-generated random
    96-bit nonce (`os.urandom` -- never fixed, never derived from the
    plaintext). Returns `nonce || ciphertext_with_tag`; the same plaintext
    encrypted twice yields two different byte strings."""
    aesgcm = AESGCM(_randomized_key())
    nonce = os.urandom(_GCM_NONCE_LENGTH)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_randomized(blob: bytes) -> str:
    """Inverse of `encrypt_randomized`: splits the leading nonce back off
    `blob` before calling AES-GCM decrypt/verify."""
    aesgcm = AESGCM(_randomized_key())
    nonce, ciphertext = blob[:_GCM_NONCE_LENGTH], blob[_GCM_NONCE_LENGTH:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


# ---------------------------------------------------------------------------
# Deterministic mode (AES-SIV -- RFC 5297) -- for `Customer.email` ONLY,
# so `Customer.email == <value>` SQL equality lookups keep working (see
# module docstring for the full tradeoff writeup).
# ---------------------------------------------------------------------------


def encrypt_deterministic(plaintext: str) -> bytes:
    """Encrypt `plaintext` with AES-SIV. The same plaintext (under the same
    key) ALWAYS returns byte-identical output -- that determinism is the
    entire point of this function's existence (see module docstring)."""
    aessiv = AESSIV(_deterministic_key())
    return aessiv.encrypt(plaintext.encode("utf-8"), None)


def decrypt_deterministic(blob: bytes) -> str:
    aessiv = AESSIV(_deterministic_key())
    return aessiv.decrypt(blob, None).decode("utf-8")


# ---------------------------------------------------------------------------
# SQLAlchemy TypeDecorators -- transparent encrypt-on-write/decrypt-on-read
# for ORM string columns. Storage type is LargeBinary (BYTEA on Postgres,
# BLOB on the SQLite dev/test fallback) since ciphertext is raw bytes, not
# text.
# ---------------------------------------------------------------------------


class EncryptedString(TypeDecorator):
    """Randomized AES-256-GCM-backed column type. NOT queryable by `==` (see
    module docstring) -- use only for columns nothing filters on by
    equality."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> bytes | None:  # noqa: ANN001
        if value is None:
            return None
        return encrypt_randomized(value)

    def process_result_value(self, value: bytes | None, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        return decrypt_randomized(value)


class DeterministicEncryptedString(TypeDecorator):
    """Deterministic AES-SIV-backed column type. Same plaintext -> same
    ciphertext, so `Column == <value>` SQL filters keep working. Use ONLY
    for columns that are genuinely queried by equality elsewhere in the
    codebase (currently: `Customer.email`, for `POST /auth/login`) -- see
    module docstring for the searchability-vs-leakage tradeoff this
    implies."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> bytes | None:  # noqa: ANN001
        if value is None:
            return None
        return encrypt_deterministic(value)

    def process_result_value(self, value: bytes | None, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        return decrypt_deterministic(value)


__all__ = [
    "EncryptionKeyError",
    "encrypt_randomized",
    "decrypt_randomized",
    "encrypt_deterministic",
    "decrypt_deterministic",
    "EncryptedString",
    "DeterministicEncryptedString",
]
