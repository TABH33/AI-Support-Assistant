"""FastAPI dependencies for authentication and role-based access control.

`require_role(*roles)` is the shared RBAC gate every endpoint that touches
customer/device/chat/ticket data is expected to sit behind (per the plan's
global constraints): `Depends(require_role("customer", "support_agent"))`.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_access_token

# auto_error=False so a missing header falls through to our own 401 (with a
# consistent body/shape) instead of FastAPI's default 403.
_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated principal extracted from a validated JWT."""

    user_id: int
    role: str
    access_level: str | None = None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """Extract, decode, and verify the bearer token from the Authorization header.

    Raises 401 if the header is missing, or the token is malformed, has an
    invalid signature, or is expired.
    """
    if credentials is None:
        raise _UNAUTHENTICATED

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _UNAUTHENTICATED from None

    sub = payload.get("sub")
    role = payload.get("role")
    if sub is None or role is None:
        raise _UNAUTHENTICATED

    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise _UNAUTHENTICATED from None

    return CurrentUser(user_id=user_id, role=role, access_level=payload.get("access_level"))


def require_role(*roles: str):
    """FastAPI dependency factory enforcing that the caller's JWT `role`
    claim is one of `roles`.

    Usage: `Depends(require_role("customer", "support_agent"))` on any route.
    Rejects with 401 if the token is missing/invalid/expired (via
    `get_current_user`), or 403 if the token is valid but its role isn't
    in the allowed set.
    """

    def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return current_user

    return _dependency
