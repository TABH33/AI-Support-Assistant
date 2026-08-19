"""Tests for Task 16: proactive reporting.

Two layers, mirroring the established patterns:
  * `app.ai.reports` unit tests: real SQLite DB round-trips via
    `SyntheticDataSource` (same in-memory-SQLite pattern as
    `test_datasource.py`), with `app.ai.reports.chat_completion` mocked --
    the only Ollama-touching call in this module -- following
    `test_chat_service.py`'s mocking pattern exactly. No test in this file
    makes a real network call; they would still pass with network access
    disabled.
  * `POST /reports/start-of-day` / `POST /reports/end-of-day` endpoint
    tests: exercise the real production `app` via `TestClient`, mirroring
    `test_chat_api.py`'s fixture pattern (in-memory SQLite `get_db`
    override, JWT tokens for customer/support_agent roles), proving RBAC
    scoping matches Task 15's `POST /chat` support_agent `customer_id`
    pattern -- including that a customer-role caller cannot escalate to
    another customer's data via a client-supplied `customer_id`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.reports import generate_end_of_day_report, generate_start_of_day_report
from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.datasources.synthetic import SyntheticDataSource
from app.main import app
from app.models import Base, Customer, Device, Driver, DrivingEvent, SupportAgent, Trip, Vehicle
from app.models.enums import (
    AccessLevel,
    BatteryStatus,
    DeviceStatus,
    DrivingEventType,
    PreferredNotificationMethod,
)

_NOW = datetime(2026, 6, 15, 9, 0, 0, tzinfo=timezone.utc)  # a Monday, 09:00 UTC


# ---------------------------------------------------------------------------
# Shared SQLite fixtures (mirrors test_datasource.py / test_chat_api.py)
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


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Report Customer {tag}",
        email=f"report-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _build_fleet(db_session: Session, *, tag: str) -> dict:
    """One customer with: two drivers, two vehicles, an unhealthy device, a
    completed trip today with a HARSH_BRAKING + SPEEDING event, an
    in-progress (unresolved) trip today with a ROUTE_DEVIATION event, and an
    idling event yesterday (fuel proxy, outside today's end-of-day window
    but inside start-of-day's 24h lookback)."""
    customer = _make_customer(db_session, tag=tag)

    driver1 = Driver(
        customer_id=customer.customer_id, full_name=f"Driver {tag}1", license_number=f"LIC-{tag}-1"
    )
    driver2 = Driver(
        customer_id=customer.customer_id, full_name=f"Driver {tag}2", license_number=f"LIC-{tag}-2"
    )
    vehicle1 = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{tag}-1",
        make=f"Make{tag}1",
        model=f"Model{tag}1",
        year=2020,
    )
    vehicle2 = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{tag}-2",
        make=f"Make{tag}2",
        model=f"Model{tag}2",
        year=2021,
    )
    db_session.add_all([driver1, driver2, vehicle1, vehicle2])
    db_session.commit()
    for obj in (driver1, driver2, vehicle1, vehicle2):
        db_session.refresh(obj)

    device_unhealthy = Device(
        customer_id=customer.customer_id,
        serial_number=f"DEV-{tag}-LOW",
        device_type="obd-ii",
        battery_status=BatteryStatus.LOW,
        device_status=DeviceStatus.ACTIVE,
    )
    db_session.add(device_unhealthy)
    db_session.commit()
    db_session.refresh(device_unhealthy)

    today_start = _NOW.replace(hour=8, minute=0, second=0, microsecond=0)
    completed_trip = Trip(
        driver_id=driver1.driver_id,
        vehicle_id=vehicle1.vehicle_id,
        start_time=today_start,
        end_time=today_start + timedelta(hours=1),
        start_location=f"{tag}-Origin",
        end_location=f"{tag}-Destination",
        distance_km=50.0,
    )
    in_progress_trip = Trip(
        driver_id=driver2.driver_id,
        vehicle_id=vehicle2.vehicle_id,
        start_time=_NOW - timedelta(hours=1),
        end_time=None,
        start_location=f"{tag}-Start2",
        end_location=None,
        distance_km=None,
    )
    db_session.add_all([completed_trip, in_progress_trip])
    db_session.commit()
    for obj in (completed_trip, in_progress_trip):
        db_session.refresh(obj)

    harsh_braking = DrivingEvent(
        trip_id=completed_trip.trip_id,
        event_type=DrivingEventType.HARSH_BRAKING,
        event_time=today_start + timedelta(minutes=10),
        details=f"{tag} harsh braking detail",
    )
    speeding = DrivingEvent(
        trip_id=completed_trip.trip_id,
        event_type=DrivingEventType.SPEEDING,
        event_time=today_start + timedelta(minutes=20),
        details=f"{tag} speeding detail",
    )
    deviation = DrivingEvent(
        trip_id=in_progress_trip.trip_id,
        event_type=DrivingEventType.ROUTE_DEVIATION,
        event_time=_NOW - timedelta(minutes=30),
        details=f"{tag} deviation detail",
    )
    db_session.add_all([harsh_braking, speeding, deviation])
    db_session.commit()

    token_customer = create_access_token(subject=customer.customer_id, role="customer")

    return {
        "customer": customer,
        "driver1": driver1,
        "driver2": driver2,
        "vehicle1": vehicle1,
        "vehicle2": vehicle2,
        "device": device_unhealthy,
        "completed_trip": completed_trip,
        "in_progress_trip": in_progress_trip,
        "headers": {"Authorization": f"Bearer {token_customer}"},
    }


@pytest.fixture()
def fleet_a(db_session):
    return _build_fleet(db_session, tag="A")


@pytest.fixture()
def fleet_b(db_session):
    return _build_fleet(db_session, tag="B")


# ---------------------------------------------------------------------------
# app.ai.reports unit tests
# ---------------------------------------------------------------------------


def test_start_of_day_report_pulls_risk_alerts_vehicle_health_and_planned_routes(
    db_session, fleet_a
):
    with patch(
        "app.ai.reports.chat_completion", return_value="A generated start-of-day summary."
    ) as mock_chat:
        report = generate_start_of_day_report(
            fleet_a["customer"].customer_id,
            db=db_session,
            data_source=SyntheticDataSource(db_session),
            now=_NOW,
        )

    assert report == "A generated start-of-day summary."
    mock_chat.assert_called_once()
    messages = mock_chat.call_args[0][0]
    context = messages[1]["content"]

    # Risk alerts (last-24h events) present.
    assert "harsh_braking" in context
    assert "speeding" in context
    assert "route_deviation" in context

    # Vehicle health section reflects the low-battery device.
    assert "need attention" in context
    assert fleet_a["device"].serial_number in context

    # Unresolved incidents: the in-progress trip's route_deviation event.
    assert "Unresolved incidents" in context
    assert f"trip {fleet_a['in_progress_trip'].trip_id}" in context

    # Planned routes: today's trips with driver/vehicle/location detail.
    assert "Planned routes" in context
    assert "A-Origin" in context
    assert "A-Destination" in context


def test_start_of_day_report_notes_empty_sections_explicitly(db_session):
    customer = _make_customer(db_session, tag="EMPTY")

    with patch("app.ai.reports.chat_completion", return_value="summary") as mock_chat:
        generate_start_of_day_report(
            customer.customer_id,
            db=db_session,
            data_source=SyntheticDataSource(db_session),
            now=_NOW,
        )

    context = mock_chat.call_args[0][0][1]["content"]
    assert "no driving events in the last 24 hours" in context
    assert "no trips currently in progress" in context
    assert "no trips scheduled/started today" in context


def test_end_of_day_report_pulls_event_type_breakdown_and_driver_performance(db_session, fleet_a):
    with patch(
        "app.ai.reports.chat_completion", return_value="A generated end-of-day summary."
    ) as mock_chat:
        report = generate_end_of_day_report(
            fleet_a["customer"].customer_id,
            db=db_session,
            data_source=SyntheticDataSource(db_session),
            now=_NOW,
        )

    assert report == "A generated end-of-day summary."
    mock_chat.assert_called_once()
    context = mock_chat.call_args[0][0][1]["content"]

    assert "Speeding: 1" in context
    assert "Harsh braking: 1" in context
    assert "Route deviations: 1" in context
    assert "0 idling event(s)" in context

    assert fleet_a["driver1"].full_name in context
    assert fleet_a["driver2"].full_name in context


def test_reports_never_leak_another_customers_data(db_session, fleet_a, fleet_b):
    """Cross-tenant isolation proof, mirroring test_chat_api.py's own
    cross-tenant tests: customer A's report context must never contain
    customer B's driver names, device serials, or trip locations."""
    with patch("app.ai.reports.chat_completion", return_value="summary") as mock_chat:
        generate_start_of_day_report(
            fleet_a["customer"].customer_id,
            db=db_session,
            data_source=SyntheticDataSource(db_session),
            now=_NOW,
        )
    start_of_day_context = mock_chat.call_args[0][0][1]["content"]

    with patch("app.ai.reports.chat_completion", return_value="summary") as mock_chat:
        generate_end_of_day_report(
            fleet_a["customer"].customer_id,
            db=db_session,
            data_source=SyntheticDataSource(db_session),
            now=_NOW,
        )
    end_of_day_context = mock_chat.call_args[0][0][1]["content"]

    for context in (start_of_day_context, end_of_day_context):
        assert fleet_b["driver1"].full_name not in context
        assert fleet_b["driver2"].full_name not in context
        assert fleet_b["device"].serial_number not in context
        assert "B-Origin" not in context
        assert "B-Destination" not in context


# ---------------------------------------------------------------------------
# Endpoint tests: POST /reports/start-of-day, POST /reports/end-of-day
# ---------------------------------------------------------------------------


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
def support_agent_headers(db_session):
    agent = SupportAgent(
        full_name="Report Test Support Agent",
        email="report-support-agent@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    token = create_access_token(
        subject=agent.support_agent_id, role="support_agent", access_level="tier_2"
    )
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_request_is_rejected_for_both_endpoints(client):
    assert client.post("/reports/start-of-day", json={}).status_code == 401
    assert client.post("/reports/end-of-day", json={}).status_code == 401


def test_customer_gets_own_start_of_day_report(client, fleet_a):
    with patch("app.ai.reports.chat_completion", return_value="Customer A's report."):
        response = client.post("/reports/start-of-day", json={}, headers=fleet_a["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == fleet_a["customer"].customer_id
    assert body["report"] == "Customer A's report."


def test_customer_gets_own_end_of_day_report(client, fleet_a):
    with patch("app.ai.reports.chat_completion", return_value="Customer A's EOD report."):
        response = client.post("/reports/end-of-day", json={}, headers=fleet_a["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == fleet_a["customer"].customer_id
    assert body["report"] == "Customer A's EOD report."


def test_customer_supplied_customer_id_is_ignored_no_privilege_escalation(
    client, fleet_a, fleet_b
):
    """Mirrors Task 15's own privilege-escalation guard: a customer-role
    caller passing another customer's id in the request body must NOT get
    that other customer's report -- they are always scoped to their own
    JWT-derived customer_id."""
    with patch("app.ai.reports.chat_completion", return_value="report") as mock_chat:
        response = client.post(
            "/reports/start-of-day",
            json={"customer_id": fleet_b["customer"].customer_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == fleet_a["customer"].customer_id
    assert body["customer_id"] != fleet_b["customer"].customer_id

    # And the data actually pulled was fleet A's, not fleet B's.
    context = mock_chat.call_args[0][0][1]["content"]
    assert fleet_b["driver1"].full_name not in context


def test_support_agent_must_supply_customer_id(client, support_agent_headers):
    response = client.post(
        "/reports/start-of-day", json={}, headers=support_agent_headers
    )
    assert response.status_code == 400


def test_support_agent_can_request_report_for_a_specific_customer(
    client, fleet_a, support_agent_headers
):
    with patch("app.ai.reports.chat_completion", return_value="Agent-requested report."):
        response = client.post(
            "/reports/end-of-day",
            json={"customer_id": fleet_a["customer"].customer_id},
            headers=support_agent_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == fleet_a["customer"].customer_id
    assert body["report"] == "Agent-requested report."


def test_customer_role_forbidden_from_other_roles_endpoint_is_not_applicable_but_wrong_role_is_403(
    client, db_session
):
    """A role outside ("customer", "support_agent") is rejected with 403 --
    reusing `require_role`'s own behavior (already unit-proven elsewhere);
    this just confirms it's actually wired onto both report routes."""
    token = create_access_token(subject=1, role="some_other_role")
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/reports/start-of-day", json={}, headers=headers).status_code == 403
    assert client.post("/reports/end-of-day", json={}, headers=headers).status_code == 403
