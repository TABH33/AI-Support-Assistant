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
from app.models import AuditLog, Base, Customer, Driver, RoutePlan
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


# ---------------------------------------------------------------------------
# Task 2: POST /route-plan persists a RoutePlan row and returns its id.
# ---------------------------------------------------------------------------


def test_successful_route_plan_is_persisted_and_returns_its_id(client, customer_headers, db_session):
    result = RoutePlanResult(distance_km=23.4, duration_min=38.2, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=customer_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["route_plan_id"], int)

    saved = db_session.get(RoutePlan, body["route_plan_id"])
    assert saved is not None
    assert saved.origin_label == "Sydney CBD"
    assert saved.destination_label == "Parramatta"
    assert saved.status.value == "active"
    assert saved.created_by_role == "customer"


def test_unavailable_route_plan_is_still_persisted(client, customer_headers, db_session):
    result = RoutePlanResult(distance_km=None, duration_min=None, geometry=None, unavailable=True)
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Nowhere", "destination": "Parramatta"},
            headers=customer_headers,
        )

    body = response.json()
    saved = db_session.get(RoutePlan, body["route_plan_id"])
    assert saved is not None
    assert saved.unavailable is True


def test_support_agent_must_supply_customer_id(client, db_session):
    from app.auth.security import create_access_token

    agent_token = create_access_token(subject=1, role="support_agent")
    headers = {"Authorization": f"Bearer {agent_token}"}

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=headers,
        )

    assert response.status_code == 400
    assert "customer_id" in response.json()["detail"]


def test_support_agent_can_save_a_route_plan_for_a_named_customer(client, db_session):
    from app.auth.security import create_access_token, hash_password
    from app.models.enums import PreferredNotificationMethod

    customer = Customer(
        full_name="Agent-Scoped Customer",
        email="agent-scoped-customer@example.test",
        phone_number="+61000000002",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)

    agent_token = create_access_token(subject=99, role="support_agent")
    headers = {"Authorization": f"Bearer {agent_token}"}

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "customer_id": customer.customer_id,
            },
            headers=headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.customer_id == customer.customer_id
    assert saved.created_by_role == "support_agent"


# ---------------------------------------------------------------------------
# Live tracking: POST /route-plan accepts an optional driver_id, validated
# against the resolved customer's own fleet.
# ---------------------------------------------------------------------------


@pytest.fixture()
def driver_customer(db_session):
    """A customer returned as the row itself (rather than only headers, as
    `customer_headers` above does) so a Driver can be created in its fleet
    and its id asserted against the saved RoutePlan."""
    customer = Customer(
        full_name="Driver Fleet Customer",
        email="driver-fleet-customer@example.test",
        phone_number="+61000000003",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture()
def driver_customer_headers(driver_customer):
    token = create_access_token(subject=driver_customer.customer_id, role="customer")
    return {"Authorization": f"Bearer {token}"}


def _make_driver(db_session, *, customer_id: int, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer_id,
        full_name=f"Fleet Driver {tag}",
        license_number=f"LIC-ROUTE-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def test_route_plan_accepts_a_driver_from_the_callers_own_fleet(
    client, db_session, driver_customer, driver_customer_headers
):
    driver = _make_driver(db_session, customer_id=driver_customer.customer_id, tag="OWN")
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "driver_id": driver.driver_id,
            },
            headers=driver_customer_headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.driver_id == driver.driver_id


def test_route_plan_rejects_a_driver_from_another_customers_fleet(
    client, db_session, driver_customer, driver_customer_headers
):
    other_customer = Customer(
        full_name="Other Fleet Customer",
        email="other-fleet-customer@example.test",
        phone_number="+61000000004",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(other_customer)
    db_session.commit()
    db_session.refresh(other_customer)
    other_driver = _make_driver(db_session, customer_id=other_customer.customer_id, tag="OTHER")

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "driver_id": other_driver.driver_id,
            },
            headers=driver_customer_headers,
        )

    assert response.status_code == 400
    assert "driver_id" in response.json()["detail"]
    assert db_session.query(RoutePlan).count() == 0


def test_route_plan_rejects_a_nonexistent_driver_id(client, db_session, driver_customer_headers):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta", "driver_id": 999999},
            headers=driver_customer_headers,
        )

    assert response.status_code == 400
    assert "driver_id" in response.json()["detail"]
    assert db_session.query(RoutePlan).count() == 0


def test_route_plan_without_a_driver_id_saves_an_unassigned_plan(
    client, db_session, driver_customer_headers
):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=driver_customer_headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.driver_id is None


def test_support_agent_driver_is_validated_against_the_named_customer(client, db_session):
    customer = Customer(
        full_name="Agent Driver Customer",
        email="agent-driver-customer@example.test",
        phone_number="+61000000005",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    driver = _make_driver(db_session, customer_id=customer.customer_id, tag="AGENT")

    agent_token = create_access_token(subject=99, role="support_agent")
    headers = {"Authorization": f"Bearer {agent_token}"}

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "customer_id": customer.customer_id,
                "driver_id": driver.driver_id,
            },
            headers=headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.driver_id == driver.driver_id
    assert saved.customer_id == customer.customer_id
