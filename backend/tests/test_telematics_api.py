"""Tests for Task 7: Core CRUD APIs for telematics data.

Mirrors `test_auth.py`'s pattern: exercises the *real* production `app`
from `app.main` via FastAPI's `TestClient`, with `get_db` overridden to a
temporary in-memory SQLite session (no live Postgres required).

Two customers ("A" and "B") are each given a full, separate fleet (driver,
vehicle, trip, driving event, device) so that every listing/detail endpoint
can be proven to isolate customer A's data from customer B's -- not just
asserted by inspection of the route code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, Customer, Device, Driver, DrivingEvent, SupportAgent, Trip, Vehicle
from app.models.enums import (
    AccessLevel,
    BatteryStatus,
    DeviceStatus,
    DrivingEventType,
    PreferredNotificationMethod,
)

# ---------------------------------------------------------------------------
# Shared SQLite fixtures (mirrors backend/tests/test_auth.py)
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
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        from fastapi.testclient import TestClient

        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Two isolated customer fleets
# ---------------------------------------------------------------------------


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Fleet Test Customer {tag}",
        email=f"fleet-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture()
def fleet_a(db_session):
    return _build_fleet(db_session, tag="A")


@pytest.fixture()
def fleet_b(db_session):
    return _build_fleet(db_session, tag="B")


def _build_fleet(db_session: Session, *, tag: str) -> dict:
    customer = _make_customer(db_session, tag=tag)

    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Driver {tag}",
        license_number=f"LIC-{tag}-001",
        email=f"driver-{tag.lower()}@example.test",
        phone_number="+61000000001",
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{tag}-001",
        make="TestMake",
        model="TestModel",
        year=2020,
    )
    db_session.add_all([driver, vehicle])
    db_session.commit()
    db_session.refresh(driver)
    db_session.refresh(vehicle)

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trip = Trip(
        driver_id=driver.driver_id,
        vehicle_id=vehicle.vehicle_id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        start_location=f"Origin {tag}",
        end_location=f"Destination {tag}",
        distance_km=42.5,
    )
    db_session.add(trip)
    db_session.commit()
    db_session.refresh(trip)

    driving_event = DrivingEvent(
        trip_id=trip.trip_id,
        event_type=DrivingEventType.HARSH_BRAKING,
        event_time=start + timedelta(minutes=10),
        location=f"Mid-route {tag}",
        details="synthetic test event",
    )
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"SEED-{tag}-DEV-001",
        device_type="obd2",
        battery_status=BatteryStatus.OK,
        device_status=DeviceStatus.ACTIVE,
    )
    db_session.add_all([driving_event, device])
    db_session.commit()
    db_session.refresh(driving_event)
    db_session.refresh(device)

    token = create_access_token(subject=customer.customer_id, role="customer")

    return {
        "customer": customer,
        "driver": driver,
        "vehicle": vehicle,
        "trip": trip,
        "driving_event": driving_event,
        "device": device,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
def support_agent(db_session):
    agent = SupportAgent(
        full_name="Fleet Test Support Agent",
        email="fleet-support-agent@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    token = create_access_token(
        subject=agent.support_agent_id, role="support_agent", access_level="tier_2"
    )
    return {"agent": agent, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


# ---------------------------------------------------------------------------
# Unauthenticated access is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/drivers", "/drivers/1", "/vehicles", "/trips", "/trips/1/events", "/devices"],
)
def test_unauthenticated_request_is_rejected(client, path):
    response = client.get(path)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Happy path: a customer sees their own fleet
# ---------------------------------------------------------------------------


def test_list_drivers_happy_path(client, fleet_a):
    response = client.get("/drivers", headers=fleet_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["driver_id"] == fleet_a["driver"].driver_id
    assert body[0]["customer_id"] == fleet_a["customer"].customer_id


def test_get_driver_by_id_happy_path(client, fleet_a):
    response = client.get(f"/drivers/{fleet_a['driver'].driver_id}", headers=fleet_a["headers"])
    assert response.status_code == 200
    assert response.json()["license_number"] == fleet_a["driver"].license_number


def test_list_vehicles_happy_path(client, fleet_a):
    response = client.get("/vehicles", headers=fleet_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["vehicle_id"] == fleet_a["vehicle"].vehicle_id


def test_list_trips_happy_path(client, fleet_a):
    response = client.get("/trips", headers=fleet_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["trip_id"] == fleet_a["trip"].trip_id


def test_list_trips_filter_by_driver_id(client, fleet_a):
    response = client.get(
        "/trips", params={"driver_id": fleet_a["driver"].driver_id}, headers=fleet_a["headers"]
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    response_miss = client.get(
        "/trips", params={"driver_id": fleet_a["driver"].driver_id + 999}, headers=fleet_a["headers"]
    )
    assert response_miss.status_code == 200
    assert response_miss.json() == []


def test_list_trips_filter_by_vehicle_id(client, fleet_a):
    response = client.get(
        "/trips", params={"vehicle_id": fleet_a["vehicle"].vehicle_id}, headers=fleet_a["headers"]
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_trips_filter_by_another_customers_driver_id_returns_empty(client, fleet_a, fleet_b):
    """Regression test: a customer-role caller supplying another customer's
    driver_id as a /trips filter must not leak that customer's trip -- the
    join-based customer_id scope predicate ANDs with the client-supplied
    driver_id filter, so no row can satisfy both, and the response is an
    empty list (not 403/404 -- the filter combination is simply
    unsatisfiable, same as any other "excluded from the list" case)."""
    response = client.get(
        "/trips", params={"driver_id": fleet_b["driver"].driver_id}, headers=fleet_a["headers"]
    )
    assert response.status_code == 200
    assert response.json() == []


def test_list_trips_filter_by_another_customers_vehicle_id_returns_empty(client, fleet_a, fleet_b):
    """Same regression as above, for the vehicle_id filter."""
    response = client.get(
        "/trips", params={"vehicle_id": fleet_b["vehicle"].vehicle_id}, headers=fleet_a["headers"]
    )
    assert response.status_code == 200
    assert response.json() == []


def test_list_trip_events_happy_path(client, fleet_a):
    response = client.get(f"/trips/{fleet_a['trip'].trip_id}/events", headers=fleet_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "harsh_braking"
    assert body[0]["driving_event_id"] == fleet_a["driving_event"].driving_event_id


def test_list_devices_happy_path(client, fleet_a):
    response = client.get("/devices", headers=fleet_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["device_id"] == fleet_a["device"].device_id


# ---------------------------------------------------------------------------
# Two-customer isolation: customer A must never see customer B's data
# ---------------------------------------------------------------------------


def test_customer_cannot_list_another_customers_drivers(client, fleet_a, fleet_b):
    response = client.get("/drivers", headers=fleet_a["headers"])
    ids = [row["driver_id"] for row in response.json()]
    assert fleet_b["driver"].driver_id not in ids
    assert ids == [fleet_a["driver"].driver_id]


def test_customer_cannot_get_another_customers_driver_by_id(client, fleet_a, fleet_b):
    response = client.get(f"/drivers/{fleet_b['driver'].driver_id}", headers=fleet_a["headers"])
    assert response.status_code == 404


def test_customer_cannot_list_another_customers_vehicles(client, fleet_a, fleet_b):
    response = client.get("/vehicles", headers=fleet_a["headers"])
    ids = [row["vehicle_id"] for row in response.json()]
    assert fleet_b["vehicle"].vehicle_id not in ids
    assert ids == [fleet_a["vehicle"].vehicle_id]


def test_customer_cannot_list_another_customers_trips(client, fleet_a, fleet_b):
    response = client.get("/trips", headers=fleet_a["headers"])
    ids = [row["trip_id"] for row in response.json()]
    assert fleet_b["trip"].trip_id not in ids
    assert ids == [fleet_a["trip"].trip_id]


def test_customer_cannot_view_another_customers_trip_events(client, fleet_a, fleet_b):
    response = client.get(f"/trips/{fleet_b['trip'].trip_id}/events", headers=fleet_a["headers"])
    assert response.status_code == 404


def test_customer_cannot_list_another_customers_devices(client, fleet_a, fleet_b):
    response = client.get("/devices", headers=fleet_a["headers"])
    ids = [row["device_id"] for row in response.json()]
    assert fleet_b["device"].device_id not in ids
    assert ids == [fleet_a["device"].device_id]


def test_customer_supplied_customer_id_filter_on_devices_is_ignored(client, fleet_a, fleet_b):
    """A customer-role caller can't use the ?customer_id= query param to see
    another customer's devices -- the JWT-derived id always wins."""
    response = client.get(
        "/devices", params={"customer_id": fleet_b["customer"].customer_id}, headers=fleet_a["headers"]
    )
    assert response.status_code == 200
    ids = [row["device_id"] for row in response.json()]
    assert ids == [fleet_a["device"].device_id]


# ---------------------------------------------------------------------------
# support_agent sees across customers
# ---------------------------------------------------------------------------


def test_support_agent_sees_drivers_across_customers(client, fleet_a, fleet_b, support_agent):
    response = client.get("/drivers", headers=support_agent["headers"])
    assert response.status_code == 200
    ids = {row["driver_id"] for row in response.json()}
    assert {fleet_a["driver"].driver_id, fleet_b["driver"].driver_id} <= ids


def test_support_agent_sees_vehicles_across_customers(client, fleet_a, fleet_b, support_agent):
    response = client.get("/vehicles", headers=support_agent["headers"])
    ids = {row["vehicle_id"] for row in response.json()}
    assert {fleet_a["vehicle"].vehicle_id, fleet_b["vehicle"].vehicle_id} <= ids


def test_support_agent_sees_trips_across_customers(client, fleet_a, fleet_b, support_agent):
    response = client.get("/trips", headers=support_agent["headers"])
    ids = {row["trip_id"] for row in response.json()}
    assert {fleet_a["trip"].trip_id, fleet_b["trip"].trip_id} <= ids


def test_support_agent_can_view_any_customers_trip_events(client, fleet_a, fleet_b, support_agent):
    response = client.get(
        f"/trips/{fleet_b['trip'].trip_id}/events", headers=support_agent["headers"]
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_support_agent_sees_devices_across_customers(client, fleet_a, fleet_b, support_agent):
    response = client.get("/devices", headers=support_agent["headers"])
    ids = {row["device_id"] for row in response.json()}
    assert {fleet_a["device"].device_id, fleet_b["device"].device_id} <= ids


def test_support_agent_can_filter_devices_by_customer_id(client, fleet_a, fleet_b, support_agent):
    response = client.get(
        "/devices",
        params={"customer_id": fleet_b["customer"].customer_id},
        headers=support_agent["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["device_id"] == fleet_b["device"].device_id


def test_support_agent_can_get_any_driver_by_id(client, fleet_a, fleet_b, support_agent):
    response = client.get(f"/drivers/{fleet_b['driver'].driver_id}", headers=support_agent["headers"])
    assert response.status_code == 200
