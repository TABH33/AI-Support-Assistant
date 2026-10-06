"""Tests for `app.ai.route_planning.summarize_my_vehicle` and
`summarize_active_drivers` -- the chat "what am I driving" and "active
drivers" intents (app/api/chat.py's _detect_my_vehicle_intent /
_detect_active_drivers_intent).

Direct function tests against an in-memory SQLite session: no FastAPI, and
no LLM (neither function ever calls one). Mirrors
test_summarize_todays_routes.py's fixture style.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import summarize_active_drivers, summarize_my_vehicle
from app.auth.security import hash_password
from app.models import Base, Customer, Driver, DrivingEvent, RoutePlan, Trip, Vehicle
from app.models.enums import DrivingEventType, PreferredNotificationMethod, RoutePlanStatus


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


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Vehicle Summary Customer {tag}",
        email=f"vehicle-summary-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_vehicle(db_session: Session, *, customer: Customer, tag: str) -> Vehicle:
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{tag}",
        make="Toyota",
        model="HiAce",
        year=2022,
    )
    db_session.add(vehicle)
    db_session.commit()
    db_session.refresh(vehicle)
    return vehicle


def _make_driver(
    db_session: Session,
    *,
    customer: Customer,
    full_name: str,
    tag: str,
    assigned_vehicle: Vehicle | None = None,
) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=full_name,
        license_number=f"LIC-VEHICLE-{tag}",
        assigned_vehicle_id=assigned_vehicle.vehicle_id if assigned_vehicle else None,
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


# ---------------------------------------------------------------------------
# summarize_my_vehicle
# ---------------------------------------------------------------------------


def test_my_vehicle_reports_the_assigned_vehicle(db_session):
    customer = _make_customer(db_session, tag="A")
    vehicle = _make_vehicle(db_session, customer=customer, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A", assigned_vehicle=vehicle)

    answer = summarize_my_vehicle(db_session, driver_id=driver.driver_id, customer_id=customer.customer_id)

    assert answer == "You're driving a Toyota HiAce, plate REG-A."


def test_my_vehicle_with_no_driver_id_explains_no_vehicle_on_file():
    answer = summarize_my_vehicle(None, driver_id=None, customer_id=1)  # type: ignore[arg-type]

    assert "isn't linked to a specific driver profile" in answer


def test_my_vehicle_with_no_assigned_vehicle_asks_to_get_one_assigned(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")

    answer = summarize_my_vehicle(db_session, driver_id=driver.driver_id, customer_id=customer.customer_id)

    assert "don't have a vehicle assigned yet" in answer


def test_my_vehicle_scopes_the_driver_lookup_to_customer_id(db_session):
    """A driver_id that exists but belongs to a DIFFERENT customer_id must
    not leak that other fleet's vehicle -- defense in depth even though
    driver_id is JWT-derived, never client-supplied."""
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    vehicle_b = _make_vehicle(db_session, customer=customer_b, tag="B")
    driver_b = _make_driver(
        db_session, customer=customer_b, full_name="Bob Driver", tag="B", assigned_vehicle=vehicle_b
    )

    answer = summarize_my_vehicle(db_session, driver_id=driver_b.driver_id, customer_id=customer_a.customer_id)

    assert "don't have a vehicle assigned yet" in answer


# ---------------------------------------------------------------------------
# summarize_active_drivers
# ---------------------------------------------------------------------------


def test_active_drivers_reports_no_drivers_when_none_are_active(db_session):
    customer = _make_customer(db_session, tag="A")

    answer = summarize_active_drivers(db_session, customer_id=customer.customer_id)

    assert answer == "No drivers are actively on a route today."


def test_active_drivers_lists_each_driver_with_an_active_route_today(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    db_session.add(
        RoutePlan(
            customer_id=customer.customer_id,
            created_by_role="customer",
            created_by_id=customer.customer_id,
            driver_id=driver.driver_id,
            origin_label="Sydney CBD",
            destination_label="Parramatta",
            warnings=[{"type": "risk_zone", "severity": "high", "description": "x"}],
            unavailable=False,
            status=RoutePlanStatus.ACTIVE,
        )
    )
    db_session.commit()

    answer = summarize_active_drivers(db_session, customer_id=customer.customer_id)

    assert "1 driver(s) actively on a route today." in answer
    assert "- Alice Driver: Sydney CBD -> Parramatta, 1 warning(s) (1 high-severity)" in answer
    assert "no driving events today" in answer


def test_active_drivers_omits_completed_routes_and_unassigned_routes(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    db_session.add_all(
        [
            RoutePlan(
                customer_id=customer.customer_id,
                created_by_role="customer",
                created_by_id=customer.customer_id,
                driver_id=driver.driver_id,
                origin_label="Bondi Beach",
                destination_label="Sydney Airport",
                warnings=[],
                unavailable=False,
                status=RoutePlanStatus.COMPLETED,
            ),
            RoutePlan(
                customer_id=customer.customer_id,
                created_by_role="customer",
                created_by_id=customer.customer_id,
                driver_id=None,
                origin_label="Chatswood",
                destination_label="Macquarie Park",
                warnings=[],
                unavailable=False,
                status=RoutePlanStatus.ACTIVE,
            ),
        ]
    )
    db_session.commit()

    answer = summarize_active_drivers(db_session, customer_id=customer.customer_id)

    assert answer == "No drivers are actively on a route today."


def test_active_drivers_counts_todays_driving_events_for_that_driver(db_session):
    customer = _make_customer(db_session, tag="A")
    vehicle = _make_vehicle(db_session, customer=customer, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    db_session.add(
        RoutePlan(
            customer_id=customer.customer_id,
            created_by_role="customer",
            created_by_id=customer.customer_id,
            driver_id=driver.driver_id,
            origin_label="Sydney CBD",
            destination_label="Parramatta",
            warnings=[],
            unavailable=False,
            status=RoutePlanStatus.ACTIVE,
        )
    )
    now = datetime.now(timezone.utc)
    trip = Trip(
        driver_id=driver.driver_id,
        vehicle_id=vehicle.vehicle_id,
        start_time=now,
        end_time=None,
    )
    db_session.add(trip)
    db_session.commit()
    db_session.refresh(trip)
    db_session.add_all(
        [
            DrivingEvent(trip_id=trip.trip_id, event_type=DrivingEventType.SPEEDING, event_time=now),
            DrivingEvent(
                trip_id=trip.trip_id,
                event_type=DrivingEventType.HARSH_BRAKING,
                event_time=now + timedelta(minutes=1),
            ),
        ]
    )
    db_session.commit()

    answer = summarize_active_drivers(db_session, customer_id=customer.customer_id)

    assert "2 driving event(s) today" in answer
