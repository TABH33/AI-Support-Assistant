"""Tests for the `RoutePlan` model (backend/app/models/route_plan.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password
from app.models import Base, Customer, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_features(dbapi_connection, connection_record):  # noqa: ANN001
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


def _make_customer(db_session: Session) -> Customer:
    customer = Customer(
        full_name="Route Model Customer",
        email="route-model-customer@example.test",
        phone_number="+61000000001",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def test_route_plan_round_trips_through_the_database(db_session):
    customer = _make_customer(db_session)
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=23.4,
        duration_min=38.2,
        geometry={"type": "LineString", "coordinates": [[151.2, -33.8]]},
        warnings=[{"type": "risk_zone", "severity": "high"}],
        unavailable=False,
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)

    assert route_plan.route_plan_id is not None
    assert route_plan.status == RoutePlanStatus.ACTIVE
    assert route_plan.completed_at is None
    assert route_plan.warnings == [{"type": "risk_zone", "severity": "high"}]
    assert route_plan.created_at is not None


def test_route_plan_can_be_marked_completed(db_session):
    customer = _make_customer(db_session)
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Bondi Beach",
        unavailable=False,
        warnings=[],
    )
    db_session.add(route_plan)
    db_session.commit()

    route_plan.status = RoutePlanStatus.COMPLETED
    route_plan.completed_at = datetime.now(timezone.utc)
    db_session.commit()
    db_session.refresh(route_plan)

    assert route_plan.status == RoutePlanStatus.COMPLETED
    assert route_plan.completed_at is not None
