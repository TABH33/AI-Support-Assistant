"""Synthetic data generator (Task 5).

Builds a fully wired, in-memory graph of Task 4 ORM model instances --
`Customer`, `Device`, `Driver`, `Vehicle`, `Trip`, `DrivingEvent`,
`ChatSession`, `SupportTicket`, `Notification`, `SupportAgent`, and
`KnowledgeBaseArticle` -- that is referentially consistent *by construction*:
every link between rows is made by assigning a SQLAlchemy `relationship()`
attribute (e.g. ``device.customer = customer``) rather than poking in a raw
FK integer, so an inconsistent graph cannot be built and the actual PK/FK
integers only need to exist once the graph is added to a session and
flushed (see `app/seed/seed.py`).

This module never opens a DB session or imports `app.database`/`app.config`,
so it is independently unit-testable in memory (see
`backend/tests/test_seed.py`) without a live Postgres instance -- the
generated objects can be handed to any SQLAlchemy `Session` (including a
scratch in-memory SQLite one) and will insert cleanly.

Fictional-data conventions used throughout (data-privacy requirement, not
just style -- real Ctrack data is not available and the project's ethics/
compliance docs require synthetic-only data):
  * emails use the IANA-reserved ``example.test`` domain (RFC 2606), which
    can never resolve to a real mailbox.
  * phone numbers use the "555" exchange reserved for fictional use.
  * serial numbers / license numbers / registration numbers are all
    prefixed ``SEED-`` and sequentially numbered so they cannot be mistaken
    for real-world identifiers.
  * surnames (`LAST_NAMES`) are built from an obviously-synthetic root word
    (see `SYNTHETIC_NAME_MARKERS`, e.g. "Test", "Sample", "Mock") fused with
    a surname-shaped suffix, so every generated `Customer`/`Driver`/
    `SupportAgent` full name is unambiguously fictional on inspection (e.g.
    "Alex Testfield") while still being readable/name-shaped for demos.
  * `password_hash` values are an explicit non-hash placeholder string
    (Task 6 owns real password hashing; these rows exist only to satisfy the
    NOT NULL constraint until Task 6's auth flow sets real hashes).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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

# ---------------------------------------------------------------------------
# Volumes (per the Task 5 brief)
# ---------------------------------------------------------------------------

NUM_CUSTOMERS = 10
NUM_DEVICES = 20
NUM_DRIVERS = 15
NUM_VEHICLES = 15
NUM_TRIPS = 50
NUM_DRIVING_EVENTS = 200
NUM_SUPPORT_AGENTS = 3
# Not explicitly volumed in the brief, but required so that SupportTicket's
# NOT NULL chat_session_id/device_id FKs and Notification rows have
# something real to reference, and so the seed script populates every one
# of the 11 tables (per the brief's own Validate step).
NUM_CHAT_SESSIONS = 20
NUM_SUPPORT_TICKETS_TARGET = 10

DEFAULT_SEED = 1337

_PLACEHOLDER_PASSWORD_HASH = "seed$unhashed$placeholder-see-task-6-for-real-hashing"

FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Drew",
    "Cameron", "Skyler", "Reese", "Rowan", "Quinn", "Avery", "Peyton",
    "Emerson", "Hayden", "Kendall", "Marley", "Sage",
]

# Surnames are deliberately built from an obviously-synthetic root (never a
# real-world surname fragment) plus an ordinary surname-shaped suffix, e.g.
# "Testfield", "Sampleworth", "Mockton". This keeps generated full names
# ("Alex Testfield") readable/name-shaped enough for demo screenshots while
# making it unambiguous on inspection -- the same bar the `.test` email
# domain, "555" phone exchange, and `SEED-` identifier prefixes already
# meet -- that no generated person is or resembles a real individual.
# `SYNTHETIC_NAME_MARKERS` is exported so tests (and any future consumer)
# can assert the marker is actually present, not just eyeball the list.
SYNTHETIC_NAME_MARKERS = [
    "Test", "Sample", "Demo", "Mock", "Stub", "Dummy", "Fixture",
    "Placeholder", "Synthetic", "Fictional", "Scratch", "Sandbox", "Fake",
    "Debug", "Null", "Void", "Seed", "Example", "Cache", "Faux",
]
_SURNAME_SUFFIXES = [
    "field", "worth", "son", "ton", "wood", "ley", "ford", "well", "moor",
    "forth", "castle", "bridge", "hollow", "stone", "gate", "burg", "shire",
    "vale", "crest", "haven",
]
LAST_NAMES = [
    f"{root}{suffix}" for root, suffix in zip(SYNTHETIC_NAME_MARKERS, _SURNAME_SUFFIXES)
]

DEVICE_TYPES = ["obd-ii", "gps-tracker", "fuel-sensor", "dashcam"]

VEHICLE_MAKE_MODELS = [
    ("Toyota", "Hilux"), ("Ford", "Ranger"), ("Isuzu", "D-Max"),
    ("Nissan", "Navara"), ("Mitsubishi", "Triton"), ("Mazda", "BT-50"),
    ("Volkswagen", "Amarok"), ("Toyota", "Corolla"), ("Hyundai", "i30"),
    ("Kia", "Sportage"), ("Ford", "Transit"), ("Renault", "Trafic"),
    ("Iveco", "Daily"), ("Mercedes-Benz", "Sprinter"), ("Volvo", "FH"),
]

SITE_NAMES = [
    "Depot A", "Depot B", "Warehouse 12", "Customer Site 3",
    "Distribution Hub", "Regional Office", "Service Center",
    "Loading Bay 7", "North Yard", "South Yard",
]

ROAD_NAMES = [
    "Main Rd", "Highway 1", "Industrial Ave", "Ring Road",
    "Coastal Hwy", "City Center", "Rural Route 9",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _full_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


@dataclass
class SeedData:
    """The full generated object graph, grouped by entity."""

    customers: list[Customer] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    drivers: list[Driver] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    trips: list[Trip] = field(default_factory=list)
    driving_events: list[DrivingEvent] = field(default_factory=list)
    chat_sessions: list[ChatSession] = field(default_factory=list)
    support_tickets: list[SupportTicket] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    support_agents: list[SupportAgent] = field(default_factory=list)
    knowledge_base_articles: list[KnowledgeBaseArticle] = field(default_factory=list)

    def all_objects(self) -> list[Base]:
        """Every generated row, flattened -- convenient for `session.add_all(...)`."""
        return [
            *self.customers,
            *self.devices,
            *self.drivers,
            *self.vehicles,
            *self.trips,
            *self.driving_events,
            *self.chat_sessions,
            *self.support_tickets,
            *self.notifications,
            *self.support_agents,
            *self.knowledge_base_articles,
        ]


# ---------------------------------------------------------------------------
# Per-entity generators
# ---------------------------------------------------------------------------


def _generate_customers(rng: random.Random) -> list[Customer]:
    methods = list(PreferredNotificationMethod)
    customers = []
    for i in range(NUM_CUSTOMERS):
        customers.append(
            Customer(
                full_name=_full_name(rng),
                email=f"customer-{i + 1:02d}@example.test",
                phone_number=f"+1-555-{100 + i:03d}-{1000 + i:04d}",
                preferred_notification_method=methods[i % len(methods)],
                password_hash=_PLACEHOLDER_PASSWORD_HASH,
            )
        )
    return customers


def _generate_devices(rng: random.Random, customers: list[Customer]) -> list[Device]:
    # NUM_DEVICES / NUM_CUSTOMERS = 2 -> every customer gets exactly 2 devices.
    battery_choices = [BatteryStatus.OK, BatteryStatus.LOW, BatteryStatus.CRITICAL]
    status_choices = list(DeviceStatus)
    devices = []
    for i in range(NUM_DEVICES):
        customer = customers[i % len(customers)]
        # Force the first few devices to cover every enum value explicitly,
        # then let the rest vary with a realistic (mostly-healthy) skew.
        if i < len(battery_choices):
            battery_status = battery_choices[i]
        else:
            battery_status = rng.choices(battery_choices, weights=[0.7, 0.2, 0.1])[0]
        if i < len(status_choices):
            device_status = status_choices[i]
        else:
            device_status = rng.choices(
                status_choices, weights=[0.7, 0.1, 0.1, 0.1]
            )[0]

        never_reported = i % 9 == 8  # a handful of devices with no telemetry yet
        signal_strength = None if never_reported else rng.randint(0, 100)
        last_seen = None if never_reported else _now() - timedelta(
            minutes=rng.randint(1, 60 * 24 * 7)
        )

        device = Device(
            serial_number=f"SEED-DEV-{i + 1:04d}",
            device_type=DEVICE_TYPES[i % len(DEVICE_TYPES)],
            battery_status=battery_status,
            signal_strength=signal_strength,
            last_seen=last_seen,
            device_status=device_status,
            installed_at=_now() - timedelta(days=rng.randint(10, 400)),
        )
        device.customer = customer
        devices.append(device)
    return devices


def _generate_drivers(rng: random.Random) -> list[Driver]:
    drivers = []
    for i in range(NUM_DRIVERS):
        has_email = rng.random() < 0.8
        has_phone = rng.random() < 0.8
        drivers.append(
            Driver(
                full_name=_full_name(rng),
                license_number=f"SEED-LIC-{i + 1:04d}",
                email=f"driver-{i + 1:02d}@example.test" if has_email else None,
                phone_number=(
                    f"+1-555-{200 + i:03d}-{2000 + i:04d}" if has_phone else None
                ),
            )
        )
    return drivers


def _generate_vehicles(rng: random.Random) -> list[Vehicle]:
    vehicles = []
    for i in range(NUM_VEHICLES):
        make, model = VEHICLE_MAKE_MODELS[i % len(VEHICLE_MAKE_MODELS)]
        vehicles.append(
            Vehicle(
                registration_number=f"SEED-REG-{i + 1:04d}",
                make=make,
                model=model,
                year=rng.randint(2015, 2024),
            )
        )
    return vehicles


def _generate_trips(
    rng: random.Random, drivers: list[Driver], vehicles: list[Vehicle]
) -> list[Trip]:
    trips = []
    for _ in range(NUM_TRIPS):
        start_time = _now() - timedelta(
            days=rng.randint(0, 60), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        in_progress = rng.random() < 0.15
        end_time = None
        distance_km = None
        if not in_progress:
            duration_minutes = rng.randint(15, 240)
            end_time = start_time + timedelta(minutes=duration_minutes)
            avg_speed_kmh = rng.uniform(30, 90)
            distance_km = round(avg_speed_kmh * duration_minutes / 60, 2)

        trip = Trip(
            start_time=start_time,
            end_time=end_time,
            start_location=rng.choice(SITE_NAMES),
            end_location=rng.choice(SITE_NAMES),
            distance_km=distance_km,
        )
        trip.driver = rng.choice(drivers)
        trip.vehicle = rng.choice(vehicles)
        trips.append(trip)
    return trips


_EVENT_DETAIL_TEMPLATES = {
    DrivingEventType.SPEEDING: lambda rng: (
        f"Recorded speed {rng.randint(95, 140)} km/h in a "
        f"{rng.choice([60, 80, 100, 110])} km/h zone."
    ),
    DrivingEventType.HARSH_BRAKING: lambda rng: (
        f"Harsh braking event: deceleration of {rng.uniform(0.4, 0.9):.2f}g detected."
    ),
    DrivingEventType.IDLING: lambda rng: (
        f"Vehicle idled for {rng.randint(5, 45)} minutes with the engine running."
    ),
    DrivingEventType.ROUTE_DEVIATION: lambda rng: (
        f"Deviated {rng.uniform(1.5, 15.0):.1f} km from the planned route."
    ),
}


def _generate_driving_events(rng: random.Random, trips: list[Trip]) -> list[DrivingEvent]:
    types = list(DrivingEventType)
    per_type = NUM_DRIVING_EVENTS // len(types)
    event_type_pool = types * per_type
    remainder = NUM_DRIVING_EVENTS - len(event_type_pool)
    event_type_pool += types[:remainder]
    rng.shuffle(event_type_pool)

    events = []
    for event_type in event_type_pool:
        trip = rng.choice(trips)
        window_end = trip.end_time or (trip.start_time + timedelta(hours=2))
        window_seconds = max(int((window_end - trip.start_time).total_seconds()), 1)
        event_time = trip.start_time + timedelta(seconds=rng.randint(0, window_seconds))

        event = DrivingEvent(
            event_type=event_type,
            event_time=event_time,
            location=rng.choice(ROAD_NAMES),
            details=_EVENT_DETAIL_TEMPLATES[event_type](rng),
        )
        event.trip = trip
        events.append(event)
    return events


def _generate_knowledge_base_articles() -> list[KnowledgeBaseArticle]:
    # Fixed, hand-written content (not randomized) covering the ASS3 Sec 4.2
    # support scenarios named in the brief: device pairing, harsh-braking
    # alerts, and GPS signal loss -- plus a few adjacent FAQ topics so the
    # RAG corpus has reasonable topical breadth for Task 11+.
    articles = [
        (
            "How to pair your telematics device",
            (
                "1. Power on the device and ensure it is within range of your "
                "vehicle's OBD-II port or mounting bracket. 2. Open the fleet "
                "app and select 'Add Device'. 3. Enter the device's serial "
                "number (printed on the underside of the unit). 4. Wait for "
                "the pairing confirmation, indicated by a solid green LED. "
                "Pairing typically completes within 60 seconds."
            ),
            "device_pairing",
        ),
        (
            "Device pairing troubleshooting: device won't connect",
            (
                "If a device fails to pair: confirm the vehicle's ignition is "
                "on, move the device closer to the receiver, restart the "
                "fleet app, and check that the device's battery status is "
                "not 'critical' (a critically low battery can prevent the "
                "initial handshake). If pairing still fails after three "
                "attempts, raise a support ticket with the device's serial "
                "number."
            ),
            "device_pairing",
        ),
        (
            "Understanding harsh braking alerts",
            (
                "A harsh braking event is recorded when the device detects a "
                "deceleration greater than 0.4g. This threshold is tuned to "
                "flag sudden stops rather than normal traffic slowing. Each "
                "alert includes the trip, approximate location, and severity "
                "of the deceleration."
            ),
            "harsh_braking",
        ),
        (
            "Harsh braking alert troubleshooting: too many false alerts",
            (
                "Frequent harsh-braking alerts on smooth roads can indicate "
                "the device is loosely mounted, which causes vibration to be "
                "misread as deceleration. Re-seat the device in its mount, "
                "confirm the mounting bracket is tightened, and contact "
                "support if the false alerts continue after remounting."
            ),
            "harsh_braking",
        ),
        (
            "GPS signal loss: common causes",
            (
                "GPS signal loss is most often caused by driving through "
                "tunnels, underground parking, dense urban canyons, or "
                "covered loading bays. Signal typically recovers within a "
                "few minutes of returning to open sky. Persistent loss in "
                "open areas can indicate an antenna fault."
            ),
            "gps_signal_loss",
        ),
        (
            "GPS signal loss troubleshooting steps",
            (
                "If a device shows no GPS fix for more than 30 minutes in an "
                "open area: 1. Check the device's signal strength reading in "
                "the app. 2. Power-cycle the device. 3. Inspect the antenna "
                "cable for visible damage. 4. If the device status shows "
                "'offline' for over an hour, raise a support ticket for a "
                "possible hardware fault."
            ),
            "gps_signal_loss",
        ),
        (
            "Understanding your device's battery status",
            (
                "Each device reports a battery status of 'ok', 'low', or "
                "'critical'. 'Low' means the device should be checked at the "
                "next scheduled maintenance. 'Critical' means the device may "
                "stop reporting telemetry within hours and should be "
                "serviced immediately."
            ),
            "device_maintenance",
        ),
        (
            "Idling alerts explained",
            (
                "An idling event is logged when a vehicle's engine runs for "
                "an extended period without the vehicle moving. Idling "
                "alerts help fleet managers identify unnecessary fuel "
                "consumption and emissions."
            ),
            "driving_events",
        ),
        (
            "Route deviation alerts explained",
            (
                "A route deviation event is recorded when a trip's actual "
                "path differs from its planned route by more than a "
                "configured distance threshold. This can flag detours, "
                "traffic rerouting, or unauthorized stops."
            ),
            "driving_events",
        ),
        (
            "How to update your notification preferences",
            (
                "You can change how you're notified about alerts and support "
                "ticket updates (email, SMS, push, or in-app) from your "
                "account settings. Changes take effect immediately for new "
                "notifications; notifications already queued for delivery "
                "are unaffected."
            ),
            "account",
        ),
    ]
    return [
        KnowledgeBaseArticle(title=title, content=content, category=category)
        for title, content, category in articles
    ]


def _generate_support_agents(rng: random.Random) -> list[SupportAgent]:
    levels = list(AccessLevel)
    agents = []
    for i in range(NUM_SUPPORT_AGENTS):
        agents.append(
            SupportAgent(
                full_name=_full_name(rng),
                email=f"agent-{i + 1:02d}@example.test",
                access_level=levels[i % len(levels)],
                password_hash=_PLACEHOLDER_PASSWORD_HASH,
            )
        )
    return agents


def _generate_chat_sessions(
    rng: random.Random, customers: list[Customer], devices: list[Device]
) -> list[ChatSession]:
    devices_by_customer: dict[int, list[Device]] = {}
    for device in devices:
        devices_by_customer.setdefault(id(device.customer), []).append(device)

    sessions = []
    for _ in range(NUM_CHAT_SESSIONS):
        customer = rng.choice(customers)
        device = rng.choice(devices_by_customer[id(customer)])
        is_ended = rng.random() < 0.7
        start_time = _now() - timedelta(
            days=rng.randint(0, 30), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        end_time = None
        ai_confidence_score = None
        if is_ended:
            end_time = start_time + timedelta(minutes=rng.randint(1, 30))
            ai_confidence_score = round(rng.uniform(0.5, 0.99), 2)

        session = ChatSession(
            session_status=SessionStatus.ENDED if is_ended else SessionStatus.ACTIVE,
            start_time=start_time,
            end_time=end_time,
            ai_confidence_score=ai_confidence_score,
        )
        session.customer = customer
        session.device = device
        sessions.append(session)
    return sessions


_TICKET_SCENARIOS = [
    ("Device won't pair", "Customer reports the device will not complete pairing."),
    ("Too many harsh braking alerts", "Customer disputes the frequency of harsh braking alerts."),
    ("GPS signal keeps dropping", "Customer reports repeated GPS signal loss on open roads."),
    ("Battery status shows critical", "Customer's device is reporting a critical battery status."),
    ("General account question", "Customer has a general question about their account."),
]


def _generate_support_tickets(
    rng: random.Random, chat_sessions: list[ChatSession], agents: list[SupportAgent]
) -> list[SupportTicket]:
    ended_sessions = [s for s in chat_sessions if s.session_status == SessionStatus.ENDED]
    target = min(NUM_SUPPORT_TICKETS_TARGET, len(ended_sessions))
    chosen_sessions = rng.sample(ended_sessions, target) if target else []

    statuses = list(TicketStatus)
    priorities = list(Priority)

    tickets = []
    for i, session in enumerate(chosen_sessions):
        subject, description = _TICKET_SCENARIOS[i % len(_TICKET_SCENARIOS)]
        status = statuses[i % len(statuses)]
        priority = priorities[i % len(priorities)]
        created_at = session.end_time or session.start_time
        resolved_at = None
        if status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            resolved_at = created_at + timedelta(hours=rng.randint(1, 72))

        ticket = SupportTicket(
            ticket_status=status,
            priority=priority,
            subject=subject,
            description=description,
            created_at=created_at,
            resolved_at=resolved_at,
        )
        ticket.chat_session = session
        ticket.customer = session.customer
        ticket.device = session.device
        if rng.random() < 0.8:
            ticket.assigned_support_agent = rng.choice(agents)
        tickets.append(ticket)
    return tickets


def _generate_notifications(
    rng: random.Random, support_tickets: list[SupportTicket]
) -> list[Notification]:
    methods = list(PreferredNotificationMethod)
    notifications = []
    for ticket in support_tickets:
        for j in range(rng.randint(1, 3)):
            notification_type = (
                ticket.customer.preferred_notification_method
                if rng.random() < 0.7
                else rng.choice(methods)
            )
            has_sent = rng.random() < 0.85
            sent_at = (
                ticket.created_at + timedelta(minutes=rng.randint(1, 120))
                if has_sent
                else None
            )
            notification = Notification(
                notification_type=notification_type,
                message=(
                    f"Update on ticket '{ticket.subject}': status is now "
                    f"'{ticket.ticket_status.value}'."
                ),
                sent_at=sent_at,
            )
            notification.support_ticket = ticket
            notification.customer = ticket.customer
            notifications.append(notification)
    return notifications


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_seed_data(seed: int = DEFAULT_SEED) -> SeedData:
    """Generate a full, referentially-consistent synthetic data graph.

    Deterministic for a given `seed` (default reproducible for tests/CI).
    Returns plain, unpersisted ORM objects wired together via relationship
    attributes; nothing here touches a database session.
    """
    rng = random.Random(seed)

    customers = _generate_customers(rng)
    devices = _generate_devices(rng, customers)
    drivers = _generate_drivers(rng)
    vehicles = _generate_vehicles(rng)
    trips = _generate_trips(rng, drivers, vehicles)
    driving_events = _generate_driving_events(rng, trips)
    knowledge_base_articles = _generate_knowledge_base_articles()
    support_agents = _generate_support_agents(rng)
    chat_sessions = _generate_chat_sessions(rng, customers, devices)
    support_tickets = _generate_support_tickets(rng, chat_sessions, support_agents)
    notifications = _generate_notifications(rng, support_tickets)

    return SeedData(
        customers=customers,
        devices=devices,
        drivers=drivers,
        vehicles=vehicles,
        trips=trips,
        driving_events=driving_events,
        chat_sessions=chat_sessions,
        support_tickets=support_tickets,
        notifications=notifications,
        support_agents=support_agents,
        knowledge_base_articles=knowledge_base_articles,
    )
