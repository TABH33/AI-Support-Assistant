"""Tests for the Task 5 synthetic data generator and seed data invariants.

Two layers of testing, mirroring backend/tests/test_models.py's approach:
  1. Pure in-memory checks against the generator's output objects directly
     (no DB at all) -- proves the generator itself builds a consistent
     object graph, independent of any database.
  2. A DB-backed round-trip test against an in-memory SQLite database (with
     `PRAGMA foreign_keys=ON`) -- proves every FK in the generated graph
     actually resolves once persisted, not just "no exception on
     construction".

No live Postgres is required or used.
"""

from collections import Counter

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    ChatSession,
    Customer,
    Device,
    Driver,
    DrivingEvent,
    KnowledgeBaseArticle,
    Notification,
    SupportAgent,
    SupportTicket,
    Trip,
    Vehicle,
)
from app.models.enums import (
    AccessLevel,
    BatteryStatus,
    DeviceStatus,
    DrivingEventType,
    PreferredNotificationMethod,
    Priority,
    SessionStatus,
    TicketStatus,
)
from app.seed.generator import (
    NUM_CUSTOMERS,
    NUM_DEVICES,
    NUM_DRIVERS,
    NUM_DRIVING_EVENTS,
    NUM_SUPPORT_AGENTS,
    NUM_TRIPS,
    NUM_VEHICLES,
    SYNTHETIC_NAME_MARKERS,
    SeedData,
    generate_seed_data,
)


@pytest.fixture(scope="module")
def data() -> SeedData:
    return generate_seed_data()


# ---------------------------------------------------------------------------
# 1. Volumes match the brief
# ---------------------------------------------------------------------------


def test_generated_volumes_match_the_brief(data):
    assert len(data.customers) == NUM_CUSTOMERS == 10
    assert len(data.devices) == NUM_DEVICES == 20
    assert len(data.drivers) == NUM_DRIVERS == 15
    assert len(data.vehicles) == NUM_VEHICLES == 15
    assert len(data.trips) == NUM_TRIPS == 50
    assert len(data.driving_events) == NUM_DRIVING_EVENTS == 200
    assert len(data.knowledge_base_articles) == 10
    assert 2 <= len(data.support_agents) <= 3
    assert len(data.support_agents) == NUM_SUPPORT_AGENTS
    # Not explicitly volumed by the brief, but must be non-trivial so that
    # SupportTicket/Notification (needed for the brief's "populates all
    # tables" validation step) have real rows to reference.
    assert len(data.chat_sessions) > 0
    assert len(data.support_tickets) > 0
    assert len(data.notifications) > 0


# ---------------------------------------------------------------------------
# 2. Pure in-memory referential consistency (object identity, no DB)
# ---------------------------------------------------------------------------


def test_every_device_belongs_to_a_generated_customer(data):
    customer_ids = {id(c) for c in data.customers}
    for device in data.devices:
        assert device.customer is not None
        assert id(device.customer) in customer_ids


def test_devices_are_evenly_distributed_across_customers(data):
    counts = Counter(id(device.customer) for device in data.devices)
    assert set(counts.values()) == {2}
    assert len(counts) == NUM_CUSTOMERS


def test_every_trip_references_a_generated_driver_and_vehicle(data):
    driver_ids = {id(d) for d in data.drivers}
    vehicle_ids = {id(v) for v in data.vehicles}
    for trip in data.trips:
        assert id(trip.driver) in driver_ids
        assert id(trip.vehicle) in vehicle_ids


def test_every_driver_and_vehicle_belongs_to_a_generated_customer(data):
    """Fleet isolation (Task 4 follow-up): Driver.customer_id and
    Vehicle.customer_id are now required (NOT NULL) FKs."""
    customer_ids = {id(c) for c in data.customers}
    for driver in data.drivers:
        assert driver.customer is not None
        assert id(driver.customer) in customer_ids
    for vehicle in data.vehicles:
        assert vehicle.customer is not None
        assert id(vehicle.customer) in customer_ids


def test_every_trip_pairs_a_driver_and_vehicle_from_the_same_customer(data):
    """A trip happens within one customer's fleet -- pairing a Customer A
    driver with a Customer B vehicle would be nonsensical data and would
    undermine the whole point of the fleet-isolation FKs. This is a real
    referential-consistency check (comparing actual customer identity), not
    just "the field is populated"."""
    for trip in data.trips:
        assert trip.driver.customer is not None
        assert trip.vehicle.customer is not None
        assert trip.driver.customer is trip.vehicle.customer


def test_every_driving_event_references_a_generated_trip(data):
    trip_ids = {id(t) for t in data.trips}
    for event in data.driving_events:
        assert id(event.trip) in trip_ids
        # the event happens no earlier than the trip started
        assert event.event_time >= event.trip.start_time


def test_driving_event_types_are_evenly_covered(data):
    counts = Counter(event.event_type for event in data.driving_events)
    assert set(counts.keys()) == set(DrivingEventType)
    assert counts[DrivingEventType.SPEEDING] == 50
    assert counts[DrivingEventType.HARSH_BRAKING] == 50
    assert counts[DrivingEventType.IDLING] == 50
    assert counts[DrivingEventType.ROUTE_DEVIATION] == 50


def test_driving_events_have_coordinates_near_a_demo_corridor(data):
    from app.seed.generator import DEMO_CORRIDORS

    assert len(data.driving_events) > 0
    for event in data.driving_events:
        assert event.latitude is not None
        assert event.longitude is not None
        assert any(
            min(start[0], end[0]) - 0.01 <= event.latitude <= max(start[0], end[0]) + 0.01
            and min(start[1], end[1]) - 0.01 <= event.longitude <= max(start[1], end[1]) + 0.01
            for _, start, end in DEMO_CORRIDORS
        )


def test_every_chat_session_device_belongs_to_its_customer(data):
    for session in data.chat_sessions:
        assert session.device.customer is session.customer


def test_every_support_ticket_is_consistent_with_its_chat_session(data):
    """SupportTicket carries its own customer_id/device_id (a required,
    NOT NULL FK per the Task 4 model, not merely derivable via the chat
    session), so the generator must keep them in sync with the chat session
    the ticket was escalated from."""
    for ticket in data.support_tickets:
        assert ticket.chat_session is not None
        assert ticket.customer is ticket.chat_session.customer
        assert ticket.device is ticket.chat_session.device
        assert ticket.device is not None
        # only ENDED chat sessions produce tickets in this generator
        assert ticket.chat_session.session_status == SessionStatus.ENDED


def test_every_notification_is_consistent_with_its_ticket(data):
    for notification in data.notifications:
        assert notification.support_ticket is not None
        assert notification.customer is notification.support_ticket.customer


# ---------------------------------------------------------------------------
# 3. Enum values are all valid members
# ---------------------------------------------------------------------------


def test_all_enum_fields_hold_valid_members(data):
    for customer in data.customers:
        assert customer.preferred_notification_method in PreferredNotificationMethod
    for device in data.devices:
        assert device.battery_status in BatteryStatus
        assert device.device_status in DeviceStatus
    for event in data.driving_events:
        assert event.event_type in DrivingEventType
    for session in data.chat_sessions:
        assert session.session_status in SessionStatus
    for ticket in data.support_tickets:
        assert ticket.ticket_status in TicketStatus
        assert ticket.priority in Priority
    for notification in data.notifications:
        assert notification.notification_type in PreferredNotificationMethod
    for agent in data.support_agents:
        assert agent.access_level in AccessLevel


def test_device_battery_status_covers_all_three_values(data):
    """Explicitly called out by the task brief: Device.battery_status is an
    enum and the generator must supply valid values across ok/low/critical."""
    statuses = {device.battery_status for device in data.devices}
    assert statuses == {BatteryStatus.OK, BatteryStatus.LOW, BatteryStatus.CRITICAL}


# ---------------------------------------------------------------------------
# 4. Required (NOT NULL) fields are populated
# ---------------------------------------------------------------------------


def test_required_fields_are_populated(data):
    for customer in data.customers:
        assert customer.full_name
        assert customer.email
        assert customer.phone_number
        assert customer.password_hash
    for device in data.devices:
        assert device.serial_number
        assert device.device_type
        assert device.customer is not None
    for driver in data.drivers:
        assert driver.full_name
        assert driver.license_number
    for vehicle in data.vehicles:
        assert vehicle.registration_number
        assert vehicle.make
        assert vehicle.model
        assert vehicle.year
    for trip in data.trips:
        assert trip.start_time is not None
        assert trip.driver is not None
        assert trip.vehicle is not None
    for event in data.driving_events:
        assert event.event_time is not None
        assert event.trip is not None
    for session in data.chat_sessions:
        assert session.start_time is not None
        assert session.customer is not None
        assert session.device is not None
    for ticket in data.support_tickets:
        assert ticket.chat_session is not None
        assert ticket.customer is not None
        assert ticket.device is not None
    for notification in data.notifications:
        assert notification.message
        assert notification.support_ticket is not None
        assert notification.customer is not None
    for agent in data.support_agents:
        assert agent.full_name
        assert agent.email
        assert agent.password_hash
    for article in data.knowledge_base_articles:
        assert article.title
        assert article.content


# ---------------------------------------------------------------------------
# 5. Data is obviously fictional (privacy requirement, not just style)
# ---------------------------------------------------------------------------


def test_all_emails_use_the_reserved_test_domain(data):
    for customer in data.customers:
        assert customer.email.endswith("@example.test")
    for driver in data.drivers:
        if driver.email is not None:
            assert driver.email.endswith("@example.test")
    for agent in data.support_agents:
        assert agent.email.endswith("@example.test")


def test_identifiers_are_obviously_synthetic(data):
    for device in data.devices:
        assert device.serial_number.startswith("SEED-")
    for driver in data.drivers:
        assert driver.license_number.startswith("SEED-")
    for vehicle in data.vehicles:
        assert vehicle.registration_number.startswith("SEED-")


def test_full_names_are_obviously_fictional(data):
    """Full names must be unambiguous on inspection, not just the emails/
    phones/IDs -- e.g. "Alex Testfield" rather than a plausible real name
    like "Alex Whitfield". Every generated full name must contain one of
    the deliberately-synthetic surname markers (see `SYNTHETIC_NAME_MARKERS`
    in the generator: "Test", "Sample", "Mock", etc.)."""

    def _has_synthetic_marker(full_name: str) -> bool:
        return any(marker in full_name for marker in SYNTHETIC_NAME_MARKERS)

    for customer in data.customers:
        assert _has_synthetic_marker(customer.full_name), customer.full_name
    for driver in data.drivers:
        assert _has_synthetic_marker(driver.full_name), driver.full_name
    for agent in data.support_agents:
        assert _has_synthetic_marker(agent.full_name), agent.full_name


# ---------------------------------------------------------------------------
# 6. Knowledge base covers the required support scenarios (ASS3 Sec 4.2)
# ---------------------------------------------------------------------------


def test_knowledge_base_covers_required_scenarios(data):
    categories = {article.category for article in data.knowledge_base_articles}
    assert "device_pairing" in categories
    assert "harsh_braking" in categories
    assert "gps_signal_loss" in categories


# ---------------------------------------------------------------------------
# 7. Determinism (reproducible seed data for a given seed)
# ---------------------------------------------------------------------------


def test_generation_is_deterministic_for_a_given_seed():
    first = generate_seed_data(seed=4242)
    second = generate_seed_data(seed=4242)
    assert [c.email for c in first.customers] == [c.email for c in second.customers]
    assert [c.full_name for c in first.customers] == [c.full_name for c in second.customers]
    assert [d.serial_number for d in first.devices] == [d.serial_number for d in second.devices]
    assert [d.battery_status for d in first.devices] == [d.battery_status for d in second.devices]
    assert len(first.driving_events) == len(second.driving_events)


# ---------------------------------------------------------------------------
# 8. DB-backed round trip: every FK genuinely resolves after persistence
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
def session(engine):
    with Session(engine) as db_session:
        yield db_session


def test_full_seed_dataset_persists_with_no_fk_violations(session):
    """Insert the entire generated graph into a scratch SQLite DB (FK
    enforcement on) and confirm it commits cleanly -- i.e. no FK violations
    anywhere in the ~300+ row graph, matching the brief's Validate step
    ('running the seed script against a local test DB populates all tables
    with no FK violations') without requiring a live Postgres.
    """
    seed_data = generate_seed_data()
    session.add_all(seed_data.all_objects())
    session.commit()  # would raise IntegrityError on any FK/unique violation

    assert session.query(Customer).count() == 10
    assert session.query(Device).count() == 20
    assert session.query(Driver).count() == 15
    assert session.query(Vehicle).count() == 15
    assert session.query(Trip).count() == 50
    assert session.query(DrivingEvent).count() == 200
    assert session.query(SupportAgent).count() == NUM_SUPPORT_AGENTS
    assert session.query(KnowledgeBaseArticle).count() == 10
    assert session.query(ChatSession).count() == len(seed_data.chat_sessions)
    assert session.query(SupportTicket).count() == len(seed_data.support_tickets)
    assert session.query(Notification).count() == len(seed_data.notifications)


def test_persisted_fks_resolve_via_fresh_queries(session):
    """Fetch rows back by PK (not the same Python objects) and confirm every
    FK resolves to the correct related row, proving referential integrity
    survived the round trip rather than only holding via in-memory identity.
    """
    seed_data = generate_seed_data()
    session.add_all(seed_data.all_objects())
    session.commit()
    session.expire_all()

    for ticket in seed_data.support_tickets:
        fetched = session.get(SupportTicket, ticket.support_ticket_id)
        assert fetched.customer_id == ticket.customer.customer_id
        assert fetched.device_id == ticket.device.device_id
        assert fetched.chat_session_id == ticket.chat_session.chat_session_id
        assert fetched.device.customer_id == fetched.customer_id

    for notification in seed_data.notifications:
        fetched = session.get(Notification, notification.notification_id)
        assert fetched.support_ticket_id == notification.support_ticket.support_ticket_id
        assert fetched.customer_id == notification.customer.customer_id

    for event in seed_data.driving_events:
        fetched = session.get(DrivingEvent, event.driving_event_id)
        assert fetched.trip_id == event.trip.trip_id

    for device in seed_data.devices:
        fetched = session.get(Device, device.device_id)
        assert fetched.customer_id == device.customer.customer_id

    for driver in seed_data.drivers:
        fetched = session.get(Driver, driver.driver_id)
        assert fetched.customer_id == driver.customer.customer_id

    for vehicle in seed_data.vehicles:
        fetched = session.get(Vehicle, vehicle.vehicle_id)
        assert fetched.customer_id == vehicle.customer.customer_id

    for trip in seed_data.trips:
        fetched = session.get(Trip, trip.trip_id)
        assert fetched.driver_id == trip.driver.driver_id
        assert fetched.vehicle_id == trip.vehicle.vehicle_id
        # fleet isolation: a trip's driver and vehicle must belong to the
        # same customer -- re-verified here against DB-resolved FKs, not
        # just the in-memory objects already checked above.
        assert fetched.driver.customer_id == fetched.vehicle.customer_id
