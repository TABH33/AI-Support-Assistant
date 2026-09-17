"""Tests for `PATCH /route-plans/{id}/complete` (Task 4 of the
route-selector + daily-tracking plan)."""
from __future__ import annotations

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
        full_name=f"Complete Customer {tag}",
        email=f"complete-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_route_plan(db_session: Session, *, customer: Customer) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        warnings=[],
        unavailable=False,
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def test_owning_customer_can_mark_their_route_complete(client, db_session):
    customer = _make_customer(db_session, tag="A")
    route_plan = _make_route_plan(db_session, customer=customer)
    token = create_access_token(subject=customer.customer_id, role="customer")

    response = client.patch(
        f"/route-plans/{route_plan.route_plan_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None

    db_session.refresh(route_plan)
    assert route_plan.status == RoutePlanStatus.COMPLETED


def test_support_agent_can_complete_any_customers_route(client, db_session):
    customer = _make_customer(db_session, tag="A")
    route_plan = _make_route_plan(db_session, customer=customer)
    token = create_access_token(subject=1, role="support_agent")

    response = client.patch(
        f"/route-plans/{route_plan.route_plan_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_customer_cannot_complete_another_customers_route(client, db_session):
    owner = _make_customer(db_session, tag="A")
    other = _make_customer(db_session, tag="B")
    route_plan = _make_route_plan(db_session, customer=owner)
    token = create_access_token(subject=other.customer_id, role="customer")

    response = client.patch(
        f"/route-plans/{route_plan.route_plan_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_completing_an_already_completed_route_returns_409(client, db_session):
    customer = _make_customer(db_session, tag="A")
    route_plan = _make_route_plan(db_session, customer=customer)
    token = create_access_token(subject=customer.customer_id, role="customer")
    headers = {"Authorization": f"Bearer {token}"}

    first = client.patch(f"/route-plans/{route_plan.route_plan_id}/complete", headers=headers)
    assert first.status_code == 200

    second = client.patch(f"/route-plans/{route_plan.route_plan_id}/complete", headers=headers)
    assert second.status_code == 409


def test_completing_a_nonexistent_route_returns_404(client, db_session):
    customer = _make_customer(db_session, tag="A")
    token = create_access_token(subject=customer.customer_id, role="customer")

    response = client.patch(
        "/route-plans/999999/complete", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404
