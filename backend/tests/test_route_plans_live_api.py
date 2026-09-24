"""Tests for `GET /route-plans/live`: role-based scoping, the
active-and-available-only filter, and the computed live-position fields.

Fixture pattern copied from test_route_plans_list_api.py (same
engine/db_session/client trio, same in-memory SQLite `get_db` override).
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
from app.models import Base, Customer, Driver, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


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


#: Due-south meridian line, same fixture geometry as test_route_tracking.py:
#: distance along a meridian is linear in latitude, so an exact expected
#: position can be asserted for any progress fraction.
_GEOMETRY = {"type": "LineString", "coordinates": [[151.0, -33.0], [151.0, -34.0]]}


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Live Customer {tag}",
        email=f"live-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_driver(db_session: Session, *, customer: Customer, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Live Driver {tag}",
        license_number=f"LIC-LIVE-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def _make_route_plan(
    db_session: Session,
    *,
    customer: Customer,
    status: RoutePlanStatus = RoutePlanStatus.ACTIVE,
    created_at: datetime | None = None,
    duration_min: float | None = 60.0,
    geometry: dict | None = None,
    unavailable: bool = False,
    driver_id: int | None = None,
) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        driver_id=driver_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=10.0,
        duration_min=duration_min,
        geometry=_GEOMETRY if geometry is None else geometry,
        warnings=[],
        unavailable=unavailable,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def test_unauthenticated_request_is_rejected(client):
    response = client.get("/route-plans/live")
    assert response.status_code == 401


def test_customer_sees_only_their_own_live_routes(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=customer_a.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_a.customer_id


def test_support_agent_sees_all_customers_when_unscoped(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_support_agent_can_narrow_by_customer_id(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get(
        f"/route-plans/live?customer_id={customer_b.customer_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_b.customer_id


def test_completed_routes_are_excluded(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer, status=RoutePlanStatus.COMPLETED)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_unavailable_routes_are_excluded(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(
        db_session, customer=customer, unavailable=True, duration_min=None, geometry={}
    )

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_routes_without_geometry_points_are_excluded(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(
        db_session, customer=customer, geometry={"type": "LineString", "coordinates": []}
    )

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_progress_is_computed_from_elapsed_time(client, db_session):
    customer = _make_customer(db_session, tag="A")
    created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _make_route_plan(db_session, customer=customer, created_at=created_at, duration_min=60.0)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert len(body) == 1
    # 30 of 60 minutes elapsed; a second or two of test runtime keeps this
    # just above 50%, never below.
    assert 50.0 <= body[0]["progress_percent"] < 51.0
    assert body[0]["current_lat"] == pytest.approx(-33.5, abs=0.01)
    assert body[0]["current_lon"] == pytest.approx(151.0, abs=1e-6)


def test_progress_past_the_duration_clamps_to_one_hundred_percent(client, db_session):
    customer = _make_customer(db_session, tag="A")
    created_at = datetime.now(timezone.utc) - timedelta(minutes=600)
    _make_route_plan(db_session, customer=customer, created_at=created_at, duration_min=60.0)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert body[0]["progress_percent"] == pytest.approx(100.0, abs=1e-6)
    assert body[0]["current_lat"] == pytest.approx(-34.0, abs=1e-6)


def test_assigned_driver_name_is_joined_in(client, db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, tag="A")
    _make_route_plan(db_session, customer=customer, driver_id=driver.driver_id)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert body[0]["driver_id"] == driver.driver_id
    assert body[0]["driver_name"] == "Live Driver A"


def test_unassigned_route_has_a_null_driver_name(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert body[0]["driver_id"] is None
    assert body[0]["driver_name"] is None


def test_response_keeps_the_list_item_fields(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    row = response.json()[0]
    assert row["origin_label"] == "Sydney CBD"
    assert row["destination_label"] == "Parramatta"
    assert row["status"] == "active"
    assert row["geometry"] == _GEOMETRY
    assert row["warnings"] == []
    assert row["eta"] is not None
