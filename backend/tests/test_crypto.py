"""Tests for Task 23: `app.security.crypto`.

Covers both encryption modes' core safety properties directly (not just
"it round-trips"):
  * `encrypt_deterministic` must be genuinely deterministic -- the exact
    property `Customer.email == <value>` SQL equality lookups (and
    `POST /auth/login`) depend on. Proven here by asserting byte-identical
    ciphertext across repeated calls with the same plaintext, not just that
    decryption happens to work.
  * `encrypt_randomized` must NOT be accidentally deterministic -- proven by
    asserting DIFFERENT ciphertext across repeated calls with the same
    plaintext, which would fail if this ever silently regressed to reusing
    the deterministic path (or a fixed/derived nonce).
  * The two modes' ciphertexts are cross-checked as mutually unusable (a
    deterministic-mode ciphertext does not decrypt via the randomized
    decrypt function and vice versa), since they use independently-derived
    keys.

No network/DB access; pure unit tests against `app.config.settings`
(loaded from `backend/.env`, which sets a real `ENCRYPTION_KEY` for local
test runs -- see `backend/.env.example`).
"""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.config import settings
from app.security.crypto import (
    DeterministicEncryptedString,
    EncryptedString,
    EncryptionKeyError,
    decrypt_deterministic,
    decrypt_randomized,
    encrypt_deterministic,
    encrypt_randomized,
)

# ---------------------------------------------------------------------------
# Randomized mode (AES-256-GCM)
# ---------------------------------------------------------------------------


def test_randomized_round_trips():
    plaintext = "Alex Testfield"
    ciphertext = encrypt_randomized(plaintext)
    assert decrypt_randomized(ciphertext) == plaintext


def test_randomized_ciphertext_is_not_plaintext():
    plaintext = "+61000000099"
    ciphertext = encrypt_randomized(plaintext)
    assert ciphertext != plaintext.encode("utf-8")
    assert plaintext.encode("utf-8") not in ciphertext


def test_randomized_produces_different_ciphertext_each_call():
    """Proves this is NOT accidentally using the deterministic path (and
    that the nonce is genuinely fresh, not fixed/derived)."""
    plaintext = "same-value-every-time@example.test"
    ciphertexts = {encrypt_randomized(plaintext) for _ in range(10)}
    assert len(ciphertexts) == 10, "expected 10 distinct ciphertexts for 10 encryptions"


def test_randomized_nonce_prefix_differs_across_calls():
    """The leading 12-byte GCM nonce specifically must vary, not just the
    ciphertext as a whole (a stronger, more targeted check than 'the whole
    blob differs')."""
    plaintext = "same-value"
    nonces = {encrypt_randomized(plaintext)[:12] for _ in range(10)}
    assert len(nonces) == 10


# ---------------------------------------------------------------------------
# Deterministic mode (AES-SIV)
# ---------------------------------------------------------------------------


def test_deterministic_round_trips():
    plaintext = "deterministic-customer@example.test"
    ciphertext = encrypt_deterministic(plaintext)
    assert decrypt_deterministic(ciphertext) == plaintext


def test_deterministic_ciphertext_is_not_plaintext():
    plaintext = "hidden@example.test"
    ciphertext = encrypt_deterministic(plaintext)
    assert ciphertext != plaintext.encode("utf-8")
    assert plaintext.encode("utf-8") not in ciphertext


def test_deterministic_produces_identical_ciphertext_every_call():
    """The core property `Customer.email == <value>` SQL lookups depend on
    (see `app/security/crypto.py`'s module docstring, and
    `app/api/auth.py`'s login query)."""
    plaintext = "stable-ciphertext@example.test"
    ciphertexts = {encrypt_deterministic(plaintext) for _ in range(10)}
    assert len(ciphertexts) == 1, "expected exactly one distinct ciphertext for 10 encryptions"


def test_deterministic_different_plaintexts_produce_different_ciphertext():
    """Determinism must be per-plaintext, not a global constant output."""
    assert encrypt_deterministic("a@example.test") != encrypt_deterministic("b@example.test")


def test_deterministic_ciphertext_matches_across_fresh_encryptions_of_the_same_value():
    """Simulates the actual login flow's shape: encrypt once at 'write'
    time, encrypt again independently at 'lookup' time -- the two must be
    byte-identical for a SQL `==` filter to find the row."""
    written = encrypt_deterministic("login-lookup@example.test")
    looked_up = encrypt_deterministic("login-lookup@example.test")
    assert written == looked_up


# ---------------------------------------------------------------------------
# Cross-mode isolation: the two modes use independently-derived keys, so
# ciphertext from one is not valid input to the other's decrypt function.
# ---------------------------------------------------------------------------


def test_randomized_ciphertext_does_not_decrypt_as_deterministic():
    ciphertext = encrypt_randomized("cross-mode@example.test")
    with pytest.raises(Exception):
        decrypt_deterministic(ciphertext)


def test_deterministic_ciphertext_does_not_decrypt_as_randomized():
    ciphertext = encrypt_deterministic("cross-mode@example.test")
    with pytest.raises(Exception):
        decrypt_randomized(ciphertext)


def test_tampered_randomized_ciphertext_fails_to_decrypt():
    """AES-GCM is authenticated -- a flipped byte must be detected, not
    silently decrypted to garbage."""
    ciphertext = bytearray(encrypt_randomized("tamper-me@example.test"))
    ciphertext[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        decrypt_randomized(bytes(ciphertext))


def test_tampered_deterministic_ciphertext_fails_to_decrypt():
    ciphertext = bytearray(encrypt_deterministic("tamper-me@example.test"))
    ciphertext[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        decrypt_deterministic(bytes(ciphertext))


# ---------------------------------------------------------------------------
# Empty-string plaintext (an edge case both AES-GCM and AES-SIV must accept)
# ---------------------------------------------------------------------------


def test_randomized_round_trips_empty_string():
    assert decrypt_randomized(encrypt_randomized("")) == ""


def test_deterministic_round_trips_empty_string():
    assert decrypt_deterministic(encrypt_deterministic("")) == ""


# ---------------------------------------------------------------------------
# Key validation (`ENCRYPTION_KEY` misconfiguration)
# ---------------------------------------------------------------------------


def test_missing_encryption_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "")
    with pytest.raises(EncryptionKeyError, match="not set"):
        encrypt_randomized("anything")


def test_malformed_base64_encryption_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "not-valid-base64!!!")
    with pytest.raises(EncryptionKeyError, match="base64"):
        encrypt_randomized("anything")


def test_too_short_encryption_key_raises_clear_error(monkeypatch):
    short_key = base64.b64encode(b"short").decode()
    monkeypatch.setattr(settings, "encryption_key", short_key)
    with pytest.raises(EncryptionKeyError, match="32 bytes"):
        encrypt_randomized("anything")


# ---------------------------------------------------------------------------
# TypeDecorators -- bind/result param plumbing directly (no DB engine
# needed; `process_bind_param`/`process_result_value` are pure functions
# given a value and a dialect, which is unused by our implementation).
# ---------------------------------------------------------------------------


def test_encrypted_string_type_decorator_round_trips():
    col = EncryptedString()
    value = "Round Trip Name"
    stored = col.process_bind_param(value, None)
    assert isinstance(stored, bytes)
    assert col.process_result_value(stored, None) == value


def test_encrypted_string_type_decorator_passes_through_none():
    col = EncryptedString()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


def test_deterministic_encrypted_string_type_decorator_round_trips():
    col = DeterministicEncryptedString()
    value = "typedecorator@example.test"
    stored = col.process_bind_param(value, None)
    assert isinstance(stored, bytes)
    assert col.process_result_value(stored, None) == value


def test_deterministic_encrypted_string_type_decorator_is_stable():
    col = DeterministicEncryptedString()
    value = "typedecorator-stable@example.test"
    assert col.process_bind_param(value, None) == col.process_bind_param(value, None)


def test_deterministic_encrypted_string_type_decorator_passes_through_none():
    col = DeterministicEncryptedString()
    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None
