"""Authentication and role-based access control (Task 6).

- `app.auth.security` -- pure functions: password hashing (bcrypt) and JWT
  issuance/verification. No FastAPI dependency, so these are unit-testable
  in isolation and reusable outside of a request context (e.g. seeding).
- `app.auth.dependencies` -- the FastAPI `require_role(*roles)` dependency
  and the lower-level `get_current_user` it builds on, which extract and
  validate the bearer token from the Authorization header.
"""

from app.auth.dependencies import CurrentUser, get_current_user, require_role
from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password

__all__ = [
    "CurrentUser",
    "get_current_user",
    "require_role",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]
