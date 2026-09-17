"""Tests for the SQLAlchemy models in app.models.

Runs against an in-memory SQLite database (with `PRAGMA foreign_keys=ON`) so
these tests do not require a live Postgres+pgvector instance. See the task
report for the one documented SQLite/pgvector limitation this implies
(similarity-search operators like `<=>` are Postgres-only and are exercised
later, in Task 12's retrieval tests against real data -- this file only
verifies the `embedding` column can store and round-trip a vector, which
SQLite happily does since pgvector's SQLAlchemy type degrades to a plain
text column outside of Postgres).
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
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

ALL_MODEL_CLASSES = [
    Customer,
    Device,
    Driver,
    Vehicle,
    Trip,
    DrivingEvent,
    ChatSession,
    SupportTicket,
    Notification,
    SupportAgent,
    KnowledgeBaseArticle,
]


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


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. Every model can be instantiated (bare construction, all 11 entities)
# ---------------------------------------------------------------------------


def test_all_eleven_entities_are_defined_and_instantiable():
    """Every entity named in the Global Constraints exists as a model and can be built."""
    assert len(ALL_MODEL_CLASSES) == 11

    customer = Customer(
        full_name="Test Customer",
        email="customer@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    device = Device(
        customer_id=1,
        serial_number="SN-0001",
        device_type="obd-ii",
        battery_status=BatteryStatus.OK,
        signal_strength=87,
        last_seen=_now(),
        device_status=DeviceStatus.ACTIVE,
    )
    driver = Driver(customer_id=1, full_name="Test Driver", license_number="LIC-0001")
    vehicle = Vehicle(
        customer_id=1, registration_number="REG-0001", make="Toyota", model="Hilux", year=2022
    )
    trip = Trip(driver_id=1, vehicle_id=1, start_time=_now())
    driving_event = DrivingEvent(
        trip_id=1, event_type=DrivingEventType.SPEEDING, event_time=_now()
    )
    chat_session = ChatSession(
        customer_id=1, device_id=1, session_status=SessionStatus.ACTIVE, start_time=_now()
    )
    support_ticket = SupportTicket(
        chat_session_id=1,
        customer_id=1,
        device_id=1,
        ticket_status=TicketStatus.OPEN,
        priority=Priority.MEDIUM,
    )
    notification = Notification(
        support_ticket_id=1,
        customer_id=1,
        notification_type=PreferredNotificationMethod.EMAIL,
        message="Your ticket was created.",
    )
    support_agent = SupportAgent(
        full_name="Test Agent",
        email="agent@example.test",
        access_level=AccessLevel.TIER_1,
        password_hash="hashed",
    )
    kb_article = KnowledgeBaseArticle(title="How to pair a device", content="Steps...")

    instances = [
        customer,
        device,
        driver,
        vehicle,
        trip,
        driving_event,
        chat_session,
        support_ticket,
        notification,
        support_agent,
        kb_article,
    ]
    assert len(instances) == 11
    for instance in instances:
        assert isinstance(instance, Base)


# ---------------------------------------------------------------------------
# 2. Customer 1 -> Many Device
# ---------------------------------------------------------------------------


def test_customer_one_to_many_device(session):
    customer = Customer(
        full_name="Ada Fleet",
        email="ada@example.test",
        phone_number="+61000000001",
        preferred_notification_method=PreferredNotificationMethod.SMS,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    device1 = Device(
        customer_id=customer.customer_id,
        serial_number="SN-A",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    device2 = Device(
        customer_id=customer.customer_id,
        serial_number="SN-B",
        device_type="obd-ii",
        device_status=DeviceStatus.OFFLINE,
    )
    session.add_all([device1, device2])
    session.commit()

    session.refresh(customer)
    assert {d.serial_number for d in customer.devices} == {"SN-A", "SN-B"}
    assert device1.customer.customer_id == customer.customer_id


def test_device_telemetry_fields_persist_and_round_trip(session):
    """Device.battery_status, signal_strength, and last_seen (the three fields
    from the authoritative source spec that were missing from the first pass
    of this model) can be set explicitly and are read back correctly."""
    customer = Customer(
        full_name="Telemetry Customer",
        email="telemetry@example.test",
        phone_number="+61000000010",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    seen_at = _now()
    device = Device(
        customer_id=customer.customer_id,
        serial_number="SN-TELEMETRY",
        device_type="obd-ii",
        battery_status=BatteryStatus.CRITICAL,
        signal_strength=13,
        last_seen=seen_at,
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.commit()
    session.expire_all()

    fetched = session.get(Device, device.device_id)
    assert fetched.battery_status == BatteryStatus.CRITICAL
    assert fetched.signal_strength == 13
    assert fetched.last_seen is not None


def test_device_battery_status_defaults_when_unspecified(session):
    """battery_status has a model-level default and signal_strength/last_seen
    are nullable, so a Device can be created without supplying them."""
    customer = Customer(
        full_name="Default Telemetry Customer",
        email="defaulttelemetry@example.test",
        phone_number="+61000000011",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    device = Device(
        customer_id=customer.customer_id,
        serial_number="SN-DEFAULT",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.commit()
    session.expire_all()

    fetched = session.get(Device, device.device_id)
    assert fetched.battery_status == BatteryStatus.OK
    assert fetched.signal_strength is None
    assert fetched.last_seen is None


# ---------------------------------------------------------------------------
# 3. Driver 1 -> Many Trip, Trip -> Vehicle, Trip 1 -> Many DrivingEvent
# ---------------------------------------------------------------------------


def test_driver_trip_vehicle_and_driving_events(session):
    customer = Customer(
        full_name="Fleet Owner",
        email="fleetowner@example.test",
        phone_number="+61000000004",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    driver = Driver(
        customer_id=customer.customer_id, full_name="Sam Driver", license_number="LIC-100"
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-100",
        make="Ford",
        model="Ranger",
        year=2021,
    )
    session.add_all([driver, vehicle])
    session.flush()

    trip = Trip(
        driver_id=driver.driver_id,
        vehicle_id=vehicle.vehicle_id,
        start_time=_now(),
        distance_km=42.5,
    )
    session.add(trip)
    session.flush()

    event1 = DrivingEvent(
        trip_id=trip.trip_id, event_type=DrivingEventType.HARSH_BRAKING, event_time=_now()
    )
    event2 = DrivingEvent(
        trip_id=trip.trip_id, event_type=DrivingEventType.SPEEDING, event_time=_now()
    )
    session.add_all([event1, event2])
    session.commit()

    session.refresh(driver)
    session.refresh(trip)

    assert len(driver.trips) == 1
    assert driver.trips[0].vehicle.registration_number == "REG-100"
    assert trip.vehicle.vehicle_id == vehicle.vehicle_id
    assert driver.customer.customer_id == customer.customer_id
    assert vehicle.customer.customer_id == customer.customer_id
    assert {e.event_type for e in trip.driving_events} == {
        DrivingEventType.HARSH_BRAKING,
        DrivingEventType.SPEEDING,
    }
    for e in trip.driving_events:
        assert e.trip.trip_id == trip.trip_id


def test_driving_event_latitude_longitude_round_trip(session):
    customer = Customer(
        full_name="Geo Test Customer",
        email="geo-test@example.test",
        phone_number="+61000000005",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    driver = Driver(
        customer_id=customer.customer_id, full_name="Geo Driver", license_number="LIC-200"
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-200",
        make="Toyota",
        model="Hilux",
        year=2022,
    )
    session.add_all([driver, vehicle])
    session.flush()

    trip = Trip(driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=_now())
    session.add(trip)
    session.flush()

    event = DrivingEvent(
        trip_id=trip.trip_id,
        event_type=DrivingEventType.HARSH_BRAKING,
        event_time=_now(),
        latitude=-33.8688,
        longitude=151.2093,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    assert event.latitude == pytest.approx(-33.8688)
    assert event.longitude == pytest.approx(151.2093)


def test_driving_event_latitude_longitude_default_to_none(session):
    customer = Customer(
        full_name="Geo Test Customer 2",
        email="geo-test-2@example.test",
        phone_number="+61000000006",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    driver = Driver(
        customer_id=customer.customer_id, full_name="Geo Driver 2", license_number="LIC-201"
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-201",
        make="Ford",
        model="Ranger",
        year=2021,
    )
    session.add_all([driver, vehicle])
    session.flush()

    trip = Trip(driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=_now())
    session.add(trip)
    session.flush()

    event = DrivingEvent(
        trip_id=trip.trip_id, event_type=DrivingEventType.SPEEDING, event_time=_now()
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    assert event.latitude is None
    assert event.longitude is None


def test_customer_one_to_many_driver_and_vehicle_fleet_isolation(session):
    """Customer 1 -> Many Driver and Customer 1 -> Many Vehicle (fleet isolation FKs
    added after Task 7 hit a real per-customer-fleet-isolation requirement -- see
    telematics.py's module docstring). Re-fetches fresh copies via session.get()
    after session.expire_all() to confirm the FKs genuinely resolve via a real
    query, not just in-session Python object identity."""
    customer_a = Customer(
        full_name="Fleet A Owner",
        email="fleeta@example.test",
        phone_number="+61000000005",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    customer_b = Customer(
        full_name="Fleet B Owner",
        email="fleetb@example.test",
        phone_number="+61000000006",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add_all([customer_a, customer_b])
    session.flush()

    driver_a = Driver(
        customer_id=customer_a.customer_id, full_name="Driver A", license_number="LIC-A1"
    )
    vehicle_a = Vehicle(
        customer_id=customer_a.customer_id,
        registration_number="REG-A1",
        make="Toyota",
        model="Hilux",
        year=2020,
    )
    driver_b = Driver(
        customer_id=customer_b.customer_id, full_name="Driver B", license_number="LIC-B1"
    )
    session.add_all([driver_a, vehicle_a, driver_b])
    session.commit()

    # Re-fetch fresh copies from the DB to confirm every FK genuinely resolves
    # via a query, not just in-session identity (same pattern as
    # test_full_entity_graph_persists_and_all_fks_resolve).
    session.expire_all()

    fetched_customer_a = session.get(Customer, customer_a.customer_id)
    assert {d.driver_id for d in fetched_customer_a.drivers} == {driver_a.driver_id}
    assert {v.vehicle_id for v in fetched_customer_a.vehicles} == {vehicle_a.vehicle_id}

    fetched_driver_a = session.get(Driver, driver_a.driver_id)
    assert fetched_driver_a.customer.customer_id == customer_a.customer_id

    fetched_vehicle_a = session.get(Vehicle, vehicle_a.vehicle_id)
    assert fetched_vehicle_a.customer.customer_id == customer_a.customer_id

    # Fleet isolation: customer_b's driver must not show up under customer_a.
    assert driver_b.driver_id not in {d.driver_id for d in fetched_customer_a.drivers}
    fetched_customer_b = session.get(Customer, customer_b.customer_id)
    assert {d.driver_id for d in fetched_customer_b.drivers} == {driver_b.driver_id}


def test_invalid_driver_customer_id_is_rejected(session):
    """A Driver referencing a non-existent customer_id must fail (same FK-enforcement
    rigor as test_invalid_foreign_key_is_rejected, applied to the new column)."""
    orphan_driver = Driver(customer_id=999_999, full_name="Orphan Driver", license_number="LIC-ORPHAN")
    session.add(orphan_driver)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------------------
# 4. Customer 1 -> Many ChatSession, Device 1 -> Many ChatSession
# ---------------------------------------------------------------------------


def test_chat_session_links_customer_and_device(session):
    customer = Customer(
        full_name="Chat Customer",
        email="chat@example.test",
        phone_number="+61000000002",
        preferred_notification_method=PreferredNotificationMethod.PUSH,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()

    device = Device(
        customer_id=customer.customer_id,
        serial_number="SN-C",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()

    chat_session = ChatSession(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=SessionStatus.ACTIVE,
        start_time=_now(),
        ai_confidence_score=0.87,
    )
    session.add(chat_session)
    session.commit()

    session.refresh(customer)
    session.refresh(device)

    assert customer.chat_sessions[0].chat_session_id == chat_session.chat_session_id
    assert device.chat_sessions[0].chat_session_id == chat_session.chat_session_id
    assert chat_session.customer.customer_id == customer.customer_id
    assert chat_session.device.device_id == device.device_id
    assert chat_session.ai_confidence_score == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# 5. ChatSession 1 -> 0..1 SupportTicket, Customer 1 -> Many SupportTicket,
#    SupportAgent 1 -> Many SupportTicket, SupportTicket 1 -> Many Notification
# ---------------------------------------------------------------------------


def _make_customer_device_chat_session(session, suffix: str):
    customer = Customer(
        full_name=f"Customer {suffix}",
        email=f"customer{suffix}@example.test",
        phone_number="+61000000003",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"SN-{suffix}",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()
    chat_session = ChatSession(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=SessionStatus.ENDED,
        start_time=_now(),
        end_time=_now(),
    )
    session.add(chat_session)
    session.flush()
    return customer, device, chat_session


def test_chat_session_has_at_most_one_support_ticket(session):
    customer, device, chat_session = _make_customer_device_chat_session(session, "T1")

    agent = SupportAgent(
        full_name="Agent Smith",
        email="agent.smith@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash="hashed",
    )
    session.add(agent)
    session.flush()

    ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        assigned_support_agent_id=agent.support_agent_id,
        ticket_status=TicketStatus.OPEN,
        priority=Priority.HIGH,
    )
    session.add(ticket)
    session.commit()

    session.refresh(chat_session)
    session.refresh(agent)

    assert chat_session.support_ticket.support_ticket_id == ticket.support_ticket_id
    assert ticket.chat_session.chat_session_id == chat_session.chat_session_id
    assert ticket.customer.customer_id == customer.customer_id
    assert ticket.device.device_id == device.device_id
    assert ticket.assigned_support_agent.support_agent_id == agent.support_agent_id
    assert agent.assigned_tickets[0].support_ticket_id == ticket.support_ticket_id

    # 1 -> 0..1: a second ticket on the same chat_session must be rejected.
    duplicate_ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        ticket_status=TicketStatus.OPEN,
        priority=Priority.LOW,
    )
    session.add(duplicate_ticket)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_chat_session_without_ticket_resolves_to_none(session):
    _customer, _device, chat_session = _make_customer_device_chat_session(session, "T2")
    session.commit()
    session.refresh(chat_session)
    assert chat_session.support_ticket is None


def test_support_ticket_one_to_many_notification(session):
    customer, device, chat_session = _make_customer_device_chat_session(session, "T3")
    ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        ticket_status=TicketStatus.IN_PROGRESS,
        priority=Priority.URGENT,
    )
    session.add(ticket)
    session.flush()

    n1 = Notification(
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.EMAIL,
        message="A support agent is reviewing your ticket.",
    )
    n2 = Notification(
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.SMS,
        message="Your ticket has been updated.",
    )
    session.add_all([n1, n2])
    session.commit()

    session.refresh(ticket)
    assert len(ticket.notifications) == 2
    assert {n.notification_type for n in ticket.notifications} == {
        PreferredNotificationMethod.EMAIL,
        PreferredNotificationMethod.SMS,
    }
    for n in ticket.notifications:
        assert n.support_ticket.support_ticket_id == ticket.support_ticket_id


# ---------------------------------------------------------------------------
# 6. KnowledgeBaseArticle pgvector embedding column
# ---------------------------------------------------------------------------


def test_knowledge_base_article_without_embedding(session):
    article = KnowledgeBaseArticle(
        title="GPS signal loss troubleshooting",
        content="If your device loses GPS signal, first check...",
        category="troubleshooting",
    )
    session.add(article)
    session.commit()
    session.refresh(article)
    assert article.embedding is None


def test_knowledge_base_article_embedding_round_trips(session):
    """768-dim vector (matching nomic-embed-text) is stored and read back correctly.

    This is the one place pgvector's SQLite limitation is directly relevant:
    on SQLite, pgvector's Vector SQLAlchemy type degrades to storing the
    vector as a plain string and parsing it back on read (there is no native
    vector type, no indexing, and no `<=>` cosine-distance operator support
    on SQLite). This test only proves the column stores/round-trips a vector
    faithfully; Postgres-only behavior (ANN indexes, cosine distance queries
    used by Task 12's retrieval service) is out of scope for this SQLite-backed
    suite and can only be validated against a real Postgres+pgvector instance.
    """
    embedding = [float(i) / 1000 for i in range(768)]
    article = KnowledgeBaseArticle(
        title="Harsh braking alerts",
        content="Harsh braking is detected when...",
        category="alerts",
        embedding=embedding,
    )
    session.add(article)
    session.commit()
    session.refresh(article)

    assert article.embedding is not None
    assert len(article.embedding) == 768
    assert article.embedding[0] == pytest.approx(0.0)
    assert article.embedding[767] == pytest.approx(0.767)


# ---------------------------------------------------------------------------
# 7. Foreign keys actually resolve/enforce, not just "no exception thrown"
# ---------------------------------------------------------------------------


def test_invalid_foreign_key_is_rejected(session):
    """A Device referencing a non-existent customer_id must fail, proving the
    FK constraint is real (SQLite enforces it here because PRAGMA foreign_keys=ON)."""
    orphan_device = Device(
        customer_id=999_999,
        serial_number="SN-ORPHAN",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(orphan_device)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_full_entity_graph_persists_and_all_fks_resolve(session):
    """End-to-end smoke test: build one of every entity, wired together via
    every ER relationship listed in the task brief, and confirm every FK
    resolves to the correct related row after a round-trip through the DB.
    """
    customer = Customer(
        full_name="Full Graph Customer",
        email="fullgraph@example.test",
        phone_number="+61000000009",
        preferred_notification_method=PreferredNotificationMethod.IN_APP,
        password_hash="hashed",
    )
    agent = SupportAgent(
        full_name="Full Graph Agent",
        email="fullgraphagent@example.test",
        access_level=AccessLevel.ADMIN,
        password_hash="hashed",
    )
    kb_article = KnowledgeBaseArticle(title="Device pairing guide", content="Pair your device by...")
    session.add_all([customer, agent, kb_article])
    session.flush()

    driver = Driver(
        customer_id=customer.customer_id, full_name="Full Graph Driver", license_number="LIC-999"
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-999",
        make="Isuzu",
        model="D-Max",
        year=2023,
    )
    session.add_all([driver, vehicle])
    session.flush()

    device_last_seen = _now()
    device = Device(
        customer_id=customer.customer_id,
        serial_number="SN-999",
        device_type="obd-ii",
        battery_status=BatteryStatus.LOW,
        signal_strength=42,
        last_seen=device_last_seen,
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()

    trip = Trip(driver_id=driver.driver_id, vehicle_id=vehicle.vehicle_id, start_time=_now())
    session.add(trip)
    session.flush()

    driving_event = DrivingEvent(
        trip_id=trip.trip_id, event_type=DrivingEventType.ROUTE_DEVIATION, event_time=_now()
    )
    chat_session = ChatSession(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=SessionStatus.ENDED,
        start_time=_now(),
        end_time=_now(),
    )
    session.add_all([driving_event, chat_session])
    session.flush()

    ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        assigned_support_agent_id=agent.support_agent_id,
        ticket_status=TicketStatus.RESOLVED,
        priority=Priority.LOW,
    )
    session.add(ticket)
    session.flush()

    notification = Notification(
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.IN_APP,
        message="Ticket resolved.",
    )
    session.add(notification)
    session.commit()

    # Re-fetch fresh copies from the DB (not the same Python objects) to
    # confirm every FK genuinely resolves via a query, not just in-session identity.
    session.expire_all()

    fetched_notification = session.get(Notification, notification.notification_id)
    assert fetched_notification.support_ticket.support_ticket_id == ticket.support_ticket_id
    assert fetched_notification.customer.customer_id == customer.customer_id

    fetched_ticket = session.get(SupportTicket, ticket.support_ticket_id)
    assert fetched_ticket.chat_session.chat_session_id == chat_session.chat_session_id
    assert fetched_ticket.assigned_support_agent.support_agent_id == agent.support_agent_id
    assert fetched_ticket.device.device_id == device.device_id
    assert fetched_ticket.device.serial_number == "SN-999"

    fetched_chat_session = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched_chat_session.customer.customer_id == customer.customer_id
    assert fetched_chat_session.device.device_id == device.device_id
    assert fetched_chat_session.support_ticket.support_ticket_id == ticket.support_ticket_id

    fetched_device = session.get(Device, device.device_id)
    assert fetched_device.battery_status == BatteryStatus.LOW
    assert fetched_device.signal_strength == 42
    assert fetched_device.last_seen is not None
    assert fetched_device.support_tickets[0].support_ticket_id == ticket.support_ticket_id

    fetched_trip = session.get(Trip, trip.trip_id)
    assert fetched_trip.driver.driver_id == driver.driver_id
    assert fetched_trip.vehicle.vehicle_id == vehicle.vehicle_id
    assert fetched_trip.driving_events[0].driving_event_id == driving_event.driving_event_id

    fetched_driver = session.get(Driver, driver.driver_id)
    assert fetched_driver.customer.customer_id == customer.customer_id

    fetched_vehicle = session.get(Vehicle, vehicle.vehicle_id)
    assert fetched_vehicle.customer.customer_id == customer.customer_id

    fetched_kb_article = session.get(KnowledgeBaseArticle, kb_article.knowledge_base_article_id)
    assert fetched_kb_article.title == "Device pairing guide"
