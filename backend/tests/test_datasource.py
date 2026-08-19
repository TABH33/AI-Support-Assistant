"""Tests for `app.datasources` (Task 10).

Same in-memory SQLite fixture pattern as `test_chat_repository.py` (Task 8)
and `test_telematics_api.py` (Task 7): real DB round-trips, not mocks --
`SyntheticDataSource` is a thin SQLAlchemy wrapper, so the tests exercise
real queries against a real (if in-memory) database.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.models import (
    Base,
    Customer,
    Device,
    Driver,
    DrivingEvent,
    KnowledgeBaseArticle,
    Trip,
    Vehicle,
)
from app.models.enums import (
    BatteryStatus,
    DeviceStatus,
    DrivingEventType,
    PreferredNotificationMethod,
)


@pytest.fixture()
def engine():
    """An in-memory SQLite engine, shared across connections, with FK enforcement on."""
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
def session(engine):
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture()
def data_source(session) -> TelematicsDataSource:
    return SyntheticDataSource(session)


def _make_customer(session, suffix: str) -> Customer:
    customer = Customer(
        full_name=f"DataSource Customer {suffix}",
        email=f"ds{suffix}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    return customer


def _make_fleet(session, suffix: str) -> dict:
    """A driver + vehicle + trip + two driving events, for one customer."""
    customer = _make_customer(session, suffix)
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Driver {suffix}",
        license_number=f"LIC-{suffix}",
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{suffix}",
        make="TestMake",
        model="TestModel",
        year=2020,
    )
    session.add_all([driver, vehicle])
    session.flush()

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trip = Trip(
        driver_id=driver.driver_id,
        vehicle_id=vehicle.vehicle_id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        distance_km=42.5,
    )
    session.add(trip)
    session.flush()

    event_late = DrivingEvent(
        trip_id=trip.trip_id,
        event_type=DrivingEventType.HARSH_BRAKING,
        event_time=start + timedelta(minutes=45),
        details="second event",
    )
    event_early = DrivingEvent(
        trip_id=trip.trip_id,
        event_type=DrivingEventType.SPEEDING,
        event_time=start + timedelta(minutes=10),
        details="first event",
    )
    # Added out of chronological order on purpose, to prove ordering is by
    # event_time (not insertion/PK order).
    session.add_all([event_late, event_early])
    session.commit()

    return {
        "customer": customer,
        "driver": driver,
        "vehicle": vehicle,
        "trip": trip,
        "events": [event_early, event_late],
    }


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_synthetic_data_source_satisfies_the_protocol(session):
    ds = SyntheticDataSource(session)
    assert isinstance(ds, TelematicsDataSource)


def test_protocol_rejects_an_unrelated_object():
    class NotADataSource:
        pass

    assert not isinstance(NotADataSource(), TelematicsDataSource)


# ---------------------------------------------------------------------------
# get_driver / get_vehicle
# ---------------------------------------------------------------------------


def test_get_driver_returns_expected_driver(session, data_source):
    fleet = _make_fleet(session, "D1")
    session.expire_all()

    fetched = data_source.get_driver(fleet["driver"].driver_id)

    assert fetched is not None
    assert fetched.driver_id == fleet["driver"].driver_id
    assert fetched.full_name == "Driver D1"


def test_get_driver_returns_none_for_unknown_id(data_source):
    assert data_source.get_driver(999_999) is None


def test_get_vehicle_returns_expected_vehicle(session, data_source):
    fleet = _make_fleet(session, "V1")
    session.expire_all()

    fetched = data_source.get_vehicle(fleet["vehicle"].vehicle_id)

    assert fetched is not None
    assert fetched.registration_number == "REG-V1"


def test_get_vehicle_returns_none_for_unknown_id(data_source):
    assert data_source.get_vehicle(999_999) is None


# ---------------------------------------------------------------------------
# get_trip / get_trips_for_driver
# ---------------------------------------------------------------------------


def test_get_trip_returns_expected_trip(session, data_source):
    fleet = _make_fleet(session, "T1")
    session.expire_all()

    fetched = data_source.get_trip(fleet["trip"].trip_id)

    assert fetched is not None
    assert fetched.driver_id == fleet["driver"].driver_id
    assert fetched.vehicle_id == fleet["vehicle"].vehicle_id
    assert float(fetched.distance_km) == pytest.approx(42.5)


def test_get_trip_returns_none_for_unknown_id(data_source):
    assert data_source.get_trip(999_999) is None


def test_get_trips_for_driver_returns_only_that_drivers_trips(session, data_source):
    fleet_a = _make_fleet(session, "TA")
    fleet_b = _make_fleet(session, "TB")

    trips_a = data_source.get_trips_for_driver(fleet_a["driver"].driver_id)

    assert [t.trip_id for t in trips_a] == [fleet_a["trip"].trip_id]
    assert fleet_b["trip"].trip_id not in [t.trip_id for t in trips_a]


def test_get_trips_for_driver_returns_empty_list_for_unknown_driver(data_source):
    assert data_source.get_trips_for_driver(999_999) == []


# ---------------------------------------------------------------------------
# get_driving_events
# ---------------------------------------------------------------------------


def test_get_driving_events_returns_events_ordered_by_event_time(session, data_source):
    fleet = _make_fleet(session, "E1")
    session.expire_all()

    events = data_source.get_driving_events(fleet["trip"].trip_id)

    assert [e.details for e in events] == ["first event", "second event"]
    assert [e.event_type for e in events] == [
        DrivingEventType.SPEEDING,
        DrivingEventType.HARSH_BRAKING,
    ]


def test_get_driving_events_returns_empty_list_for_trip_with_no_events(session, data_source):
    fleet = _make_fleet(session, "E2")
    driver2 = Driver(
        customer_id=fleet["customer"].customer_id,
        full_name="Driver E2b",
        license_number="LIC-E2b",
    )
    session.add(driver2)
    session.flush()
    empty_trip = Trip(
        driver_id=driver2.driver_id,
        vehicle_id=fleet["vehicle"].vehicle_id,
        start_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    session.add(empty_trip)
    session.commit()

    assert data_source.get_driving_events(empty_trip.trip_id) == []


def test_get_driving_events_returns_empty_list_for_unknown_trip(data_source):
    assert data_source.get_driving_events(999_999) == []


# ---------------------------------------------------------------------------
# customer_id tenant scoping -- added after Task 10 review: every ID-keyed
# method optionally enforces the same transitive-join scoping
# app/api/telematics.py's `_scoped_*` helpers use, so LLM-tool-calling call
# sites (Tasks 12-15) have a real enforcement point, not just a docstring
# warning. Each test below builds two separate customers' fleets and proves
# fleet A's customer_id can't reach fleet B's data (and vice versa isn't
# needed -- the join logic is symmetric).
# ---------------------------------------------------------------------------


def test_get_driver_with_customer_id_returns_none_for_other_customer(session, data_source):
    fleet_a = _make_fleet(session, "SCD-A")
    fleet_b = _make_fleet(session, "SCD-B")

    assert (
        data_source.get_driver(
            fleet_b["driver"].driver_id, customer_id=fleet_a["customer"].customer_id
        )
        is None
    )
    # Sanity: the *right* customer_id still finds it.
    fetched = data_source.get_driver(
        fleet_b["driver"].driver_id, customer_id=fleet_b["customer"].customer_id
    )
    assert fetched is not None
    assert fetched.driver_id == fleet_b["driver"].driver_id


def test_get_vehicle_with_customer_id_returns_none_for_other_customer(session, data_source):
    fleet_a = _make_fleet(session, "SCV-A")
    fleet_b = _make_fleet(session, "SCV-B")

    assert (
        data_source.get_vehicle(
            fleet_b["vehicle"].vehicle_id, customer_id=fleet_a["customer"].customer_id
        )
        is None
    )
    fetched = data_source.get_vehicle(
        fleet_b["vehicle"].vehicle_id, customer_id=fleet_b["customer"].customer_id
    )
    assert fetched is not None
    assert fetched.vehicle_id == fleet_b["vehicle"].vehicle_id


def test_get_trip_with_customer_id_returns_none_for_other_customer(session, data_source):
    fleet_a = _make_fleet(session, "SCT-A")
    fleet_b = _make_fleet(session, "SCT-B")

    assert (
        data_source.get_trip(
            fleet_b["trip"].trip_id, customer_id=fleet_a["customer"].customer_id
        )
        is None
    )
    fetched = data_source.get_trip(
        fleet_b["trip"].trip_id, customer_id=fleet_b["customer"].customer_id
    )
    assert fetched is not None
    assert fetched.trip_id == fleet_b["trip"].trip_id


def test_get_trips_for_driver_with_customer_id_returns_empty_for_other_customer(
    session, data_source
):
    fleet_a = _make_fleet(session, "SCTD-A")
    fleet_b = _make_fleet(session, "SCTD-B")

    assert (
        data_source.get_trips_for_driver(
            fleet_b["driver"].driver_id, customer_id=fleet_a["customer"].customer_id
        )
        == []
    )
    trips = data_source.get_trips_for_driver(
        fleet_b["driver"].driver_id, customer_id=fleet_b["customer"].customer_id
    )
    assert [t.trip_id for t in trips] == [fleet_b["trip"].trip_id]


def test_get_driving_events_with_customer_id_returns_empty_for_other_customer(
    session, data_source
):
    fleet_a = _make_fleet(session, "SCE-A")
    fleet_b = _make_fleet(session, "SCE-B")

    assert (
        data_source.get_driving_events(
            fleet_b["trip"].trip_id, customer_id=fleet_a["customer"].customer_id
        )
        == []
    )
    events = data_source.get_driving_events(
        fleet_b["trip"].trip_id, customer_id=fleet_b["customer"].customer_id
    )
    assert len(events) == 2


def test_id_keyed_methods_default_to_unscoped_when_customer_id_omitted(session, data_source):
    """Regression guard for the `customer_id=None` default: confirms omitting
    it reproduces the pre-scoping (Task 10 original) behavior -- any caller
    can look up any id, same as before this fix round."""
    fleet_a = _make_fleet(session, "SCU-A")

    # No customer_id at all -- equivalent to the original unscoped calls.
    assert data_source.get_driver(fleet_a["driver"].driver_id) is not None
    assert data_source.get_vehicle(fleet_a["vehicle"].vehicle_id) is not None
    assert data_source.get_trip(fleet_a["trip"].trip_id) is not None
    assert len(data_source.get_trips_for_driver(fleet_a["driver"].driver_id)) == 1
    assert len(data_source.get_driving_events(fleet_a["trip"].trip_id)) == 2


# ---------------------------------------------------------------------------
# get_knowledge_base_articles
# ---------------------------------------------------------------------------


def _make_article(session, title: str, category: str | None) -> KnowledgeBaseArticle:
    article = KnowledgeBaseArticle(
        title=title,
        content=f"Content for {title}",
        category=category,
    )
    session.add(article)
    return article


def test_get_knowledge_base_articles_returns_full_corpus_by_default(session, data_source):
    _make_article(session, "GPS troubleshooting", "gps")
    _make_article(session, "Billing FAQ", "billing")
    session.commit()

    articles = data_source.get_knowledge_base_articles()

    assert {a.title for a in articles} == {"GPS troubleshooting", "Billing FAQ"}


def test_get_knowledge_base_articles_filters_by_category(session, data_source):
    _make_article(session, "GPS troubleshooting", "gps")
    _make_article(session, "GPS signal loss", "gps")
    _make_article(session, "Billing FAQ", "billing")
    session.commit()

    articles = data_source.get_knowledge_base_articles(category="gps")

    assert {a.title for a in articles} == {"GPS troubleshooting", "GPS signal loss"}
    assert all(a.category == "gps" for a in articles)


def test_get_knowledge_base_articles_returns_empty_list_when_none_exist(data_source):
    assert data_source.get_knowledge_base_articles() == []


# ---------------------------------------------------------------------------
# Task 16 fleet-wide listing methods: list_drivers / list_vehicles /
# list_devices / list_trips_for_customer -- real SQL-level customer_id
# scoping (required, not optional, per base.py's docstring), proven with
# the same two-customer isolation pattern as the ID-keyed methods above.
# ---------------------------------------------------------------------------


def _add_device(session, customer, suffix: str) -> Device:
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"DEV-{suffix}",
        device_type="obd-ii",
        battery_status=BatteryStatus.OK,
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.commit()
    return device


def _add_second_driver_and_vehicle(session, customer, suffix: str) -> dict:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Driver {suffix}b",
        license_number=f"LIC-{suffix}b",
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{suffix}b",
        make="TestMake2",
        model="TestModel2",
        year=2021,
    )
    session.add_all([driver, vehicle])
    session.commit()
    return {"driver": driver, "vehicle": vehicle}


def test_list_drivers_returns_only_that_customers_drivers(session, data_source):
    fleet_a = _make_fleet(session, "LDA")
    fleet_b = _make_fleet(session, "LDB")
    extra_a = _add_second_driver_and_vehicle(session, fleet_a["customer"], "LDA")

    drivers = data_source.list_drivers(fleet_a["customer"].customer_id)

    driver_ids = {d.driver_id for d in drivers}
    assert driver_ids == {fleet_a["driver"].driver_id, extra_a["driver"].driver_id}
    assert fleet_b["driver"].driver_id not in driver_ids


def test_list_drivers_returns_empty_list_for_customer_with_no_drivers(session, data_source):
    customer = _make_customer(session, "LDEMPTY")
    session.commit()
    assert data_source.list_drivers(customer.customer_id) == []


def test_list_vehicles_returns_only_that_customers_vehicles(session, data_source):
    fleet_a = _make_fleet(session, "LVA")
    fleet_b = _make_fleet(session, "LVB")
    extra_a = _add_second_driver_and_vehicle(session, fleet_a["customer"], "LVA")

    vehicles = data_source.list_vehicles(fleet_a["customer"].customer_id)

    vehicle_ids = {v.vehicle_id for v in vehicles}
    assert vehicle_ids == {fleet_a["vehicle"].vehicle_id, extra_a["vehicle"].vehicle_id}
    assert fleet_b["vehicle"].vehicle_id not in vehicle_ids


def test_list_devices_returns_only_that_customers_devices(session, data_source):
    fleet_a = _make_fleet(session, "LDVA")
    fleet_b = _make_fleet(session, "LDVB")
    device_a = _add_device(session, fleet_a["customer"], "LDVA")
    device_b = _add_device(session, fleet_b["customer"], "LDVB")

    devices = data_source.list_devices(fleet_a["customer"].customer_id)

    assert [d.device_id for d in devices] == [device_a.device_id]
    assert device_b.device_id not in [d.device_id for d in devices]


def test_list_devices_returns_empty_list_for_customer_with_no_devices(session, data_source):
    fleet = _make_fleet(session, "LDVEMPTY")
    assert data_source.list_devices(fleet["customer"].customer_id) == []


def test_list_trips_for_customer_returns_only_that_customers_trips(session, data_source):
    fleet_a = _make_fleet(session, "LTA")
    fleet_b = _make_fleet(session, "LTB")

    trips = data_source.list_trips_for_customer(fleet_a["customer"].customer_id)

    assert [t.trip_id for t in trips] == [fleet_a["trip"].trip_id]
    assert fleet_b["trip"].trip_id not in [t.trip_id for t in trips]


def test_list_trips_for_customer_returns_empty_list_for_customer_with_no_trips(session, data_source):
    customer = _make_customer(session, "LTEMPTY")
    session.commit()
    assert data_source.list_trips_for_customer(customer.customer_id) == []


def test_list_trips_for_customer_since_and_until_filter_by_start_time(session, data_source):
    customer = _make_customer(session, "LTWIN")
    driver = Driver(
        customer_id=customer.customer_id, full_name="Window Driver", license_number="LIC-LTWIN"
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-LTWIN",
        make="Make",
        model="Model",
        year=2020,
    )
    session.add_all([driver, vehicle])
    session.flush()

    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    day3 = datetime(2026, 1, 3, tzinfo=timezone.utc)
    trip_day1 = Trip(driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=day1)
    trip_day2 = Trip(driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=day2)
    trip_day3 = Trip(driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=day3)
    session.add_all([trip_day1, trip_day2, trip_day3])
    session.commit()

    # since only: day2 and day3.
    trips_since = data_source.list_trips_for_customer(customer.customer_id, since=day2)
    assert {t.trip_id for t in trips_since} == {trip_day2.trip_id, trip_day3.trip_id}

    # until only: day1 (until is exclusive).
    trips_until = data_source.list_trips_for_customer(customer.customer_id, until=day2)
    assert {t.trip_id for t in trips_until} == {trip_day1.trip_id}

    # since + until: just day2.
    trips_window = data_source.list_trips_for_customer(
        customer.customer_id, since=day2, until=day3
    )
    assert {t.trip_id for t in trips_window} == {trip_day2.trip_id}


def test_list_trips_for_customer_with_customer_id_isolates_other_customer(session, data_source):
    """Same isolation proof as the ID-keyed methods' customer_id tests --
    fleet A's customer_id cannot reach fleet B's trips, and vice versa."""
    fleet_a = _make_fleet(session, "LTISOA")
    fleet_b = _make_fleet(session, "LTISOB")

    trips_a = data_source.list_trips_for_customer(fleet_a["customer"].customer_id)
    trips_b = data_source.list_trips_for_customer(fleet_b["customer"].customer_id)

    assert [t.trip_id for t in trips_a] == [fleet_a["trip"].trip_id]
    assert [t.trip_id for t in trips_b] == [fleet_b["trip"].trip_id]
