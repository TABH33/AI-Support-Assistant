"""Tests for POST /route-plan.

Mirrors test_reports.py's fixture pattern: exercises the real production
`app` via TestClient, in-memory SQLite `get_db` override, JWT tokens for
customer/support_agent roles. `app.api.route_plan.build_route_plan` is
mocked directly (rather than mocking the underlying integrations clients
again) since this endpoint's own logic -- request parsing, response
shaping, RBAC, audit logging -- is what's under test here, not
build_route_plan's internals (already covered by test_build_route_plan.py).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import (
    GEOCODING_FAILED_TEXT,
    ROUTE_DATA_UNAVAILABLE_TEXT,
    UNAVAILABLE_REASON_GEOCODING,
    UNAVAILABLE_REASON_SERVICE,
    RoutePlanResult,
    Warning,
)
from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import AuditLog, Base, Customer
from app.models.enums import PreferredNotificationMethod


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        from fastapi.testclient import TestClient

        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def customer_headers(db_session):
    customer = Customer(
        full_name="Route Plan Customer",
        email="route-plan-customer@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    token = create_access_token(subject=customer.customer_id, role="customer")
    return {"Authorization": f"Bearer {token}"}


_GEOMETRY = {"type": "LineString", "coordinates": [[151.2093, -33.8688], [151.0011, -33.8150]]}


def test_unauthenticated_request_is_rejected(client):
    response = client.post("/route-plan", json={"origin": "Sydney CBD", "destination": "Parramatta"})
    assert response.status_code == 401


def test_successful_route_plan_returns_structured_response(client, customer_headers):
    result = RoutePlanResult(
        distance_km=23.4,
        duration_min=38.2,
        geometry=_GEOMETRY,
        warnings=[
            Warning(
                latitude=-33.84,
                longitude=151.15,
                distance_from_origin_km=12.0,
                type="risk_zone",
                severity="high",
                description="4 harsh-braking events recorded near this point.",
            )
        ],
    )
    with patch("app.api.route_plan.build_route_plan", return_value=result) as mock_build:
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=customer_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["distance_km"] == 23.4
    assert body["duration_min"] == 38.2
    assert body["geometry"] == _GEOMETRY
    assert body["unavailable"] is False
    assert len(body["warnings"]) == 1
    assert body["warnings"][0]["type"] == "risk_zone"
    assert body["warnings"][0]["location"] == {"lat": -33.84, "lon": 151.15}
    mock_build.assert_called_once()


def test_route_plan_accepts_coordinate_input(client, customer_headers):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result) as mock_build:
        response = client.post(
            "/route-plan",
            json={
                "origin": {"lat": -33.8688, "lon": 151.2093},
                "destination": {"lat": -33.8150, "lon": 151.0011},
            },
            headers=customer_headers,
        )

    assert response.status_code == 200
    call_args = mock_build.call_args
    origin_arg = call_args.args[0]
    assert origin_arg.latitude == -33.8688
    assert origin_arg.longitude == 151.2093


def test_unavailable_route_plan_returns_200_with_unavailable_flag(client, customer_headers):
    result = RoutePlanResult(distance_km=None, duration_min=None, geometry=None, unavailable=True)
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Nowhere", "destination": "Parramatta"},
            headers=customer_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["unavailable"] is True
    assert body["distance_km"] is None
    assert body["warnings"] == []


def test_route_plan_writes_audit_log_entry(client, customer_headers, db_session):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=customer_headers,
        )

    entries = db_session.query(AuditLog).filter(AuditLog.action == "route_plan_generated").all()
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# Final-review Fix 5: POST /route-plan must expose WHY a plan is unavailable,
# so a client can tell "that place doesn't exist" from "the service is down".
# ---------------------------------------------------------------------------


def test_geocoding_failure_response_carries_the_geocoding_reason(client, customer_headers):
    result = RoutePlanResult(
        distance_km=None,
        duration_min=None,
        geometry=None,
        unavailable=True,
        unavailable_reason=UNAVAILABLE_REASON_GEOCODING,
        unavailable_message=GEOCODING_FAILED_TEXT.format(place="Parramattaa"),
    )
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramattaa"},
            headers=customer_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["unavailable"] is True
    assert body["unavailable_reason"] == UNAVAILABLE_REASON_GEOCODING
    assert "Parramattaa" in body["unavailable_message"]
    assert body["unavailable_message"] != ROUTE_DATA_UNAVAILABLE_TEXT


def test_service_outage_response_carries_the_generic_service_reason(client, customer_headers):
    result = RoutePlanResult(
        distance_km=None,
        duration_min=None,
        geometry=None,
        unavailable=True,
        unavailable_reason=UNAVAILABLE_REASON_SERVICE,
        unavailable_message=ROUTE_DATA_UNAVAILABLE_TEXT,
    )
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=customer_headers,
        )

    body = response.json()
    assert body["unavailable_reason"] == UNAVAILABLE_REASON_SERVICE
    assert body["unavailable_message"] == ROUTE_DATA_UNAVAILABLE_TEXT


def test_successful_plan_response_has_null_unavailable_fields(client, customer_headers):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=customer_headers,
        )

    body = response.json()
    assert body["unavailable"] is False
    assert body["unavailable_reason"] is None
    assert body["unavailable_message"] is None
