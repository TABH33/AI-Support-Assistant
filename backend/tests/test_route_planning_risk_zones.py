"""Tests for app.ai.route_planning.evaluate_risk_zone_warnings (real DB round-trip)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import (
    RISK_ZONE_EVENT_THRESHOLD,
    SamplePoint,
    evaluate_risk_zone_warnings,
)
from app.models import Base, Customer, Driver, DrivingEvent, Trip, Vehicle
from app.models.enums import DrivingEventType, PreferredNotificationMethod


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
def session(engine):
    with Session(engine) as db_session:
        yield db_session


def _seed_trip(session):
    customer = Customer(
        full_name="Risk Zone Customer",
        email="riskzone@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    driver = Driver(customer_id=customer.customer_id, full_name="Driver", license_number="LIC-RZ")
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-RZ",
        make="Ford",
        model="Ranger",
        year=2021,
    )
    session.add_all([driver, vehicle])
    session.flush()
    trip = Trip(
        driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=datetime.now(timezone.utc)
    )
    session.add(trip)
    session.flush()
    return trip


def test_evaluate_risk_zone_warnings_flags_point_at_or_above_threshold(session):
    trip = _seed_trip(session)
    for _ in range(RISK_ZONE_EVENT_THRESHOLD):
        session.add(
            DrivingEvent(
                trip_id=trip.trip_id,
                event_type=DrivingEventType.HARSH_BRAKING,
                event_time=datetime.now(timezone.utc),
                latitude=-33.8688,
                longitude=151.2093,
            )
        )
    session.commit()

    warnings = evaluate_risk_zone_warnings(
        [SamplePoint(latitude=-33.8688, longitude=151.2093, distance_from_origin_km=0.0)], db=session
    )

    assert len(warnings) == 1
    assert warnings[0].type == "risk_zone"
    assert "harsh braking" in warnings[0].description.lower()


def test_evaluate_risk_zone_warnings_does_not_flag_below_threshold(session):
    trip = _seed_trip(session)
    session.add(
        DrivingEvent(
            trip_id=trip.trip_id,
            event_type=DrivingEventType.SPEEDING,
            event_time=datetime.now(timezone.utc),
            latitude=-33.8688,
            longitude=151.2093,
        )
    )
    session.commit()

    warnings = evaluate_risk_zone_warnings(
        [SamplePoint(latitude=-33.8688, longitude=151.2093, distance_from_origin_km=0.0)], db=session
    )

    assert warnings == []


def test_evaluate_risk_zone_warnings_ignores_distant_events(session):
    trip = _seed_trip(session)
    for _ in range(RISK_ZONE_EVENT_THRESHOLD):
        session.add(
            DrivingEvent(
                trip_id=trip.trip_id,
                event_type=DrivingEventType.SPEEDING,
                event_time=datetime.now(timezone.utc),
                latitude=-34.5,
                longitude=151.2093,
            )
        )
    session.commit()

    warnings = evaluate_risk_zone_warnings(
        [SamplePoint(latitude=-33.8688, longitude=151.2093, distance_from_origin_km=0.0)], db=session
    )

    assert warnings == []
