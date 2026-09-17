"""Tests for `GET /route-plans` (Task 3 of the route-selector +
daily-tracking plan): role-based scoping and date/status filtering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, Customer, RoutePlan
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


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"List Customer {tag}",
        email=f"list-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_route_plan(
    db_session: Session,
    *,
    customer: Customer,
    status: RoutePlanStatus = RoutePlanStatus.ACTIVE,
    created_at: datetime | None = None,
) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=10.0,
        duration_min=15.0,
        geometry={"type": "LineString", "coordinates": []},
        warnings=[],
        unavailable=False,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def test_customer_sees_only_their_own_routes(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=customer_a.customer_id, role="customer")
    response = client.get("/route-plans", headers={"Authorization": f"Bearer {token}"})

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
    response = client.get("/route-plans", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_support_agent_can_narrow_by_customer_id(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get(
        f"/route-plans?customer_id={customer_b.customer_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_b.customer_id


def test_status_filter_narrows_results(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer, status=RoutePlanStatus.ACTIVE)
    _make_route_plan(db_session, customer=customer, status=RoutePlanStatus.COMPLETED)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get(
        "/route-plans?status=completed", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "completed"


def test_date_filter_excludes_routes_from_other_days(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)
    _make_route_plan(
        db_session,
        customer=customer,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_unauthenticated_request_is_rejected(client):
    response = client.get("/route-plans")
    assert response.status_code == 401
