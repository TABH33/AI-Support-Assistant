"""Password hashing and JWT issuance/verification.

Pure functions only -- no FastAPI dependency in this module -- so they can
be unit tested directly and reused outside of a request context (e.g. by
the seed script, which needs `hash_password` to populate `password_hash`
columns).

Security notes:
  * Passwords are hashed with bcrypt (via the `bcrypt` package directly --
    a real, well-reviewed KDF, not a homemade scheme).
  * JWTs are signed with HS256 using `settings.jwt_secret`, which is loaded
    from the environment (see `app.config`) and never hardcoded here.
  * `decode_access_token` verifies both the signature and the expiry
    (PyJWT raises on either failure); callers must not treat an unverified
    decode as trusted input.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.config import settings

JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRE_MINUTES = 60

Role = Literal["customer", "support_agent"]


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt, returning a str safe to store
    in `Customer.password_hash` / `SupportAgent.password_hash`."""
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a bcrypt hash.

    Returns False (rather than raising) on a malformed/foreign hash, so a
    corrupt or legacy `password_hash` value fails login instead of 500ing.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    *,
    subject: int | str,
    role: Role,
    access_level: str | None = None,
    expires_minutes: int = DEFAULT_TOKEN_EXPIRE_MINUTES,
) -> str:
    """Issue a signed JWT carrying `sub` (user id), `role`, and -- for
    support agents -- `access_level` claims.

    `expires_minutes` may be negative (used by tests to mint an
    already-expired token).
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    if access_level is not None:
        payload["access_level"] = access_level
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT (signature + expiry).

    Raises `jwt.PyJWTError` (or a subclass, e.g. `ExpiredSignatureError`,
    `InvalidSignatureError`, `DecodeError`) on any invalid/expired/malformed
    token -- callers must not catch this and fall back to trusting the
    payload.
    """
    return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
