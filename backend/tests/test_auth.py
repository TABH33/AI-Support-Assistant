"""Tests for Task 6: Auth & RBAC.

Two layers, mirroring the pattern already used by test_models.py / test_seed.py:

  1. Login (`POST /auth/login`) is tested against the *real* production
     `app` from `app.main`, with `get_db` overridden to a temporary
     in-memory SQLite session (no live Postgres required) -- this exercises
     the actual route, the actual `Customer`/`SupportAgent` lookup, and the
     actual bcrypt verification, not mocks.
  2. `require_role` / `get_current_user` are tested against a small,
     separate FastAPI app built directly on top of `app.auth.dependencies`
     (they don't touch the DB at all), again via FastAPI's TestClient
     rather than by calling the dependency functions directly -- this
     proves the real Depends()-wiring rejects/accepts correctly, not just
     the underlying pure functions in isolation.
"""

from __future__ import annotations

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import CurrentUser, require_role
from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password
from app.database import get_db
from app.main import app
from app.models import Base, Customer, SupportAgent
from app.models.enums import AccessLevel, PreferredNotificationMethod

# ---------------------------------------------------------------------------
# Shared SQLite fixtures (mirrors backend/tests/test_models.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(engine, db_session):
    """TestClient against the real production app, with `get_db` overridden
    to hand out sessions bound to the temporary SQLite engine."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def seeded_customer(db_session):
    customer = Customer(
        full_name="Auth Test Customer",
        email="auth-customer@example.test",
        phone_number="+61000000099",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("correct-horse-battery-staple"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture()
def seeded_agent(db_session):
    agent = SupportAgent(
        full_name="Auth Test Agent",
        email="auth-agent@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash=hash_password("agent-super-secret"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


# ---------------------------------------------------------------------------
# security.py: password hashing (pure functions, no DB/HTTP)
# ---------------------------------------------------------------------------


def test_hash_password_uses_real_bcrypt_not_plaintext():
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"
    # bcrypt hashes are always prefixed with one of these version markers.
    assert hashed.startswith(("$2a$", "$2b$", "$2y$"))


def test_verify_password_round_trips():
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_rejects_malformed_hash_without_raising():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


# ---------------------------------------------------------------------------
# security.py: JWT issuance/verification (pure functions, no DB/HTTP)
# ---------------------------------------------------------------------------


def test_create_and_decode_access_token_round_trips_claims():
    token = create_access_token(subject=42, role="support_agent", access_level="tier_2")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "support_agent"
    assert payload["access_level"] == "tier_2"


def test_decode_access_token_rejects_tampered_signature():
    token = create_access_token(subject=1, role="customer")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tampered)


def test_decode_access_token_rejects_expired_token():
    token = create_access_token(subject=1, role="customer", expires_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


# ---------------------------------------------------------------------------
# POST /auth/login -- against the real app + temporary SQLite DB
# ---------------------------------------------------------------------------


def test_login_success_customer(client, seeded_customer):
    response = client.post(
        "/auth/login",
        json={"email": "auth-customer@example.test", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "customer"
    assert body["access_level"] is None
    assert body["token_type"] == "bearer"

    payload = decode_access_token(body["access_token"])
    assert payload["sub"] == str(seeded_customer.customer_id)
    assert payload["role"] == "customer"
    assert "access_level" not in payload


def test_login_success_support_agent(client, seeded_agent):
    response = client.post(
        "/auth/login",
        json={"email": "auth-agent@example.test", "password": "agent-super-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "support_agent"
    assert body["access_level"] == "tier_2"

    payload = decode_access_token(body["access_token"])
    assert payload["sub"] == str(seeded_agent.support_agent_id)
    assert payload["role"] == "support_agent"
    assert payload["access_level"] == "tier_2"


def test_login_wrong_password_customer_is_rejected(client, seeded_customer):
    response = client.post(
        "/auth/login",
        json={"email": "auth-customer@example.test", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_login_wrong_password_support_agent_is_rejected(client, seeded_agent):
    response = client.post(
        "/auth/login",
        json={"email": "auth-agent@example.test", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_login_unknown_email_gives_identical_generic_error(client, seeded_customer):
    """Security requirement: an unknown email and a known email with the
    wrong password must be indistinguishable to the caller."""
    unknown_response = client.post(
        "/auth/login",
        json={"email": "nobody-such-account@example.test", "password": "whatever"},
    )
    wrong_password_response = client.post(
        "/auth/login",
        json={"email": "auth-customer@example.test", "password": "wrong-password"},
    )
    assert unknown_response.status_code == wrong_password_response.status_code == 401
    assert unknown_response.json()["detail"] == wrong_password_response.json()["detail"]


def test_login_response_never_echoes_password_hash(client, seeded_customer):
    response = client.post(
        "/auth/login",
        json={"email": "auth-customer@example.test", "password": "correct-horse-battery-staple"},
    )
    assert seeded_customer.password_hash not in response.text


# ---------------------------------------------------------------------------
# require_role / get_current_user -- exercised through a real FastAPI app
# and TestClient (not called directly), since they don't touch the DB.
# ---------------------------------------------------------------------------


def _build_protected_app() -> FastAPI:
    protected_app = FastAPI()

    @protected_app.get("/whoami")
    def whoami(current_user: CurrentUser = Depends(require_role("customer", "support_agent"))):
        return {
            "user_id": current_user.user_id,
            "role": current_user.role,
            "access_level": current_user.access_level,
        }

    @protected_app.get("/agents-only")
    def agents_only(current_user: CurrentUser = Depends(require_role("support_agent"))):
        return {"user_id": current_user.user_id, "role": current_user.role}

    return protected_app


@pytest.fixture()
def protected_client():
    return TestClient(_build_protected_app())


def test_protected_route_rejects_missing_token(protected_client):
    response = protected_client.get("/whoami")
    assert response.status_code == 401


def test_protected_route_rejects_invalid_token(protected_client):
    response = protected_client.get(
        "/whoami", headers={"Authorization": "Bearer this-is-not-a-valid-jwt"}
    )
    assert response.status_code == 401


def test_protected_route_rejects_expired_token(protected_client):
    expired_token = create_access_token(subject=1, role="customer", expires_minutes=-1)
    response = protected_client.get(
        "/whoami", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401


def test_protected_route_accepts_valid_token_with_allowed_role(protected_client):
    token = create_access_token(subject=7, role="customer")
    response = protected_client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == 7
    assert body["role"] == "customer"


def test_require_role_rejects_role_not_in_allowed_set(protected_client):
    """A valid, unexpired customer token must still be forbidden from a
    support_agent-only route."""
    customer_token = create_access_token(subject=3, role="customer")
    response = protected_client.get(
        "/agents-only", headers={"Authorization": f"Bearer {customer_token}"}
    )
    assert response.status_code == 403


def test_require_role_allows_matching_role(protected_client):
    agent_token = create_access_token(subject=9, role="support_agent", access_level="admin")
    response = protected_client.get(
        "/agents-only", headers={"Authorization": f"Bearer {agent_token}"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "support_agent"
