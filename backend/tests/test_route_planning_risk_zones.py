"""Tests for app.ai.route_planning.evaluate_risk_zone_warnings (real DB round-trip)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import (
    RISK_ZONE_EVENT_THRESHOLD,
    RISK_ZONE_HIGH_SEVERITY_THRESHOLD,
    RISK_ZONE_RADIUS_KM,
    SamplePoint,
    evaluate_risk_zone_warnings,
)
from app.geo import haversine_distance_km
from app.models import Base, Customer, Driver, DrivingEvent, Trip, Vehicle
from app.models.enums import DrivingEventType, PreferredNotificationMethod
from app.seed.generator import DEMO_CORRIDORS, generate_seed_data


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


def test_risk_zone_severity_is_reachable_at_the_high_threshold(session):
    """`RISK_ZONE_HIGH_SEVERITY_THRESHOLD` must actually be attainable --
    the previous inline `RISK_ZONE_EVENT_THRESHOLD * 2` rule became
    unreachable dead code once the event threshold was retuned upward
    (final-review Fix 3)."""
    assert RISK_ZONE_HIGH_SEVERITY_THRESHOLD >= RISK_ZONE_EVENT_THRESHOLD

    trip = _seed_trip(session)
    for _ in range(RISK_ZONE_HIGH_SEVERITY_THRESHOLD):
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
    assert warnings[0].severity == "high"
    # The radius is rendered in metres; `{RISK_ZONE_RADIUS_KM:.0f}km` used to
    # render a sub-kilometre radius as the literal "0km".
    assert "0m of this point" in warnings[0].description
    assert "0km" not in warnings[0].description


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


# ---------------------------------------------------------------------------
# Seed-density calibration (final-review Fix 3)
#
# The unit tests above only prove the threshold is *applied*; they say
# nothing about whether it produces a useful signal against the data this
# app actually ships. This test closes that gap: with the original
# 1.0km/3-event settings every one of the 8 sampled points flagged on all 3
# demo corridors (8/8/8), so a "risk zone" covered 100% of every demo route
# and carried no information. Uses the in-memory generated seed graph
# (fixed DEFAULT_SEED) -- no DB round-trip needed.
# ---------------------------------------------------------------------------

_SAMPLE_COUNT = 8


class _InMemoryEventSource:
    """Minimal TelematicsDataSource stand-in exposing only the one method
    evaluate_risk_zone_warnings uses, backed by generate_seed_data()'s
    in-memory DrivingEvent list."""

    def __init__(self, events):
        self._events = events

    def get_driving_events_near(self, latitude, longitude, radius_km):
        return [
            e
            for e in self._events
            if e.latitude is not None
            and e.longitude is not None
            and haversine_distance_km(latitude, longitude, e.latitude, e.longitude) <= radius_km
        ]


def _corridor_sample_points(start, end, count=_SAMPLE_COUNT):
    points = []
    for i in range(count):
        t = i / (count - 1)
        lat = start[0] + (end[0] - start[0]) * t
        lon = start[1] + (end[1] - start[1]) * t
        points.append(SamplePoint(latitude=lat, longitude=lon, distance_from_origin_km=0.0))
    return points


def test_risk_zone_thresholds_flag_a_selective_subset_of_each_demo_corridor():
    seed_data = generate_seed_data()
    source = _InMemoryEventSource(seed_data.driving_events)

    flagged_per_corridor = {}
    for name, start, end in DEMO_CORRIDORS:
        warnings = evaluate_risk_zone_warnings(
            _corridor_sample_points(start, end), db=None, data_source=source
        )
        flagged_per_corridor[name] = len(warnings)

    for name, flagged in flagged_per_corridor.items():
        # Never zero (the demo must show *some* risk zones)...
        assert flagged >= 1, f"{name} flagged no risk zones at all: {flagged_per_corridor}"
        # ...and never every point (which is what the pre-fix 1.0km/3-event
        # settings produced -- 8/8 on all three corridors).
        assert flagged <= _SAMPLE_COUNT // 2, (
            f"{name} flagged {flagged}/{_SAMPLE_COUNT} points -- the risk-zone "
            f"signal is too broad to be meaningful: {flagged_per_corridor}"
        )


def test_risk_zone_radius_and_threshold_are_the_probed_values():
    """Guards the specific pair measured against the real seed data; changing
    either without re-probing silently breaks the calibration above."""
    assert RISK_ZONE_RADIUS_KM == 0.5
    assert RISK_ZONE_EVENT_THRESHOLD == 10
