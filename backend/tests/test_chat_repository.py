"""Tests for `app.repositories.chat` (Task 8).

Runs against an in-memory SQLite database (with `PRAGMA foreign_keys=ON`),
same fixture pattern as `test_models.py` (Task 4), `test_seed.py` (Task 5),
and `test_telematics_api.py` (Task 7): construct via the repository
functions, commit, `expire_all()`, then re-fetch fresh copies via
`session.get()` to prove data genuinely round-trips through the DB rather
than surviving only as in-session Python object identity.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, ChatSession, Customer, Device, Notification, SupportTicket
from app.models.enums import (
    DeviceStatus,
    PreferredNotificationMethod,
    Priority,
    SessionStatus,
    TicketStatus,
)
from app.repositories.chat import (
    create_chat_session,
    create_notification,
    create_support_ticket,
    end_chat_session,
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


def _now():
    return datetime.now(timezone.utc)


def _make_customer_and_device(session, suffix: str) -> tuple[Customer, Device]:
    customer = Customer(
        full_name=f"Repo Customer {suffix}",
        email=f"repo{suffix}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"SN-REPO-{suffix}",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()
    return customer, device


# ---------------------------------------------------------------------------
# create_chat_session
# ---------------------------------------------------------------------------


def test_create_chat_session_persists_and_round_trips(session):
    customer, device = _make_customer_and_device(session, "CS1")

    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )

    assert chat_session.chat_session_id is not None
    session.expire_all()

    fetched = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched is not None
    assert fetched.customer_id == customer.customer_id
    assert fetched.device_id == device.device_id
    assert fetched.session_status == SessionStatus.ACTIVE
    assert fetched.start_time is not None
    assert fetched.end_time is None


def test_create_chat_session_accepts_explicit_fields(session):
    customer, device = _make_customer_and_device(session, "CS2")
    start = _now()

    chat_session = create_chat_session(
        session,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=SessionStatus.ACTIVE,
        start_time=start,
        ai_confidence_score=0.42,
    )
    session.expire_all()

    fetched = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched.ai_confidence_score == pytest.approx(0.42)


def test_create_chat_session_rejects_unknown_customer(session):
    _customer, device = _make_customer_and_device(session, "CS3")

    with pytest.raises(IntegrityError):
        create_chat_session(session, customer_id=999_999, device_id=device.device_id)
    session.rollback()


# ---------------------------------------------------------------------------
# end_chat_session
# ---------------------------------------------------------------------------


def test_end_chat_session_sets_end_time_and_status(session):
    customer, device = _make_customer_and_device(session, "ES1")
    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )
    assert chat_session.end_time is None

    ended = end_chat_session(session, chat_session.chat_session_id)

    assert ended.session_status == SessionStatus.ENDED
    assert ended.end_time is not None

    session.expire_all()
    fetched = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched.session_status == SessionStatus.ENDED
    assert fetched.end_time is not None


def test_end_chat_session_accepts_explicit_end_time_and_confidence_score(session):
    customer, device = _make_customer_and_device(session, "ES2")
    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )
    end_time = _now()

    ended = end_chat_session(
        session,
        chat_session.chat_session_id,
        end_time=end_time,
        ai_confidence_score=0.91,
    )

    session.expire_all()
    fetched = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched.ai_confidence_score == pytest.approx(0.91)
    assert fetched.end_time is not None


def test_end_chat_session_unknown_id_raises_value_error(session):
    with pytest.raises(ValueError):
        end_chat_session(session, 999_999)


# ---------------------------------------------------------------------------
# create_support_ticket + ChatSession 1 -> 0..1 SupportTicket
# ---------------------------------------------------------------------------


def test_create_support_ticket_persists_and_round_trips(session):
    customer, device = _make_customer_and_device(session, "ST1")
    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )
    ended = end_chat_session(session, chat_session.chat_session_id)

    ticket = create_support_ticket(
        session,
        chat_session_id=ended.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        priority=Priority.HIGH,
        subject="GPS not updating",
        description="Device stopped reporting location an hour ago.",
    )

    assert ticket.support_ticket_id is not None
    session.expire_all()

    fetched = session.get(SupportTicket, ticket.support_ticket_id)
    assert fetched.chat_session_id == ended.chat_session_id
    assert fetched.customer_id == customer.customer_id
    assert fetched.device_id == device.device_id
    assert fetched.ticket_status == TicketStatus.OPEN
    assert fetched.priority == Priority.HIGH
    assert fetched.subject == "GPS not updating"
    assert fetched.assigned_support_agent_id is None


def test_second_support_ticket_for_same_chat_session_raises_integrity_error(session):
    """Proves ChatSession 1 -> 0..1 SupportTicket is enforced at the DB level
    (the unique constraint Task 4 put on `SupportTicket.chat_session_id`),
    not merely assumed: a genuine second insert attempt for the same
    `chat_session_id` must fail with `IntegrityError` on flush.

    `create_chat_session`/`create_support_ticket` only `db.flush()`, not
    `db.commit()` (final-review Fix 6 -- see `app/repositories/chat.py`'s
    module docstring: the caller owns the transaction boundary). This test
    commits explicitly right after the setup it needs to survive, so the
    `session.rollback()` below -- which is scoped to undoing the FAILED
    second insert, per this test's own point -- doesn't also wipe out the
    chat_session/first_ticket rows it's asserting still exist afterward.
    """
    customer, device = _make_customer_and_device(session, "ST2")
    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )

    first_ticket = create_support_ticket(
        session,
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
    )
    assert first_ticket.support_ticket_id is not None
    session.commit()

    with pytest.raises(IntegrityError):
        create_support_ticket(
            session,
            chat_session_id=chat_session.chat_session_id,
            customer_id=customer.customer_id,
            device_id=device.device_id,
        )
    session.rollback()

    # The first ticket is still the only one on record.
    session.expire_all()
    fetched_chat_session = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched_chat_session.support_ticket.support_ticket_id == first_ticket.support_ticket_id


# ---------------------------------------------------------------------------
# create_notification + SupportTicket 1 -> Many Notification
# ---------------------------------------------------------------------------


def test_create_notification_persists_and_round_trips(session):
    customer, device = _make_customer_and_device(session, "N1")
    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )
    ticket = create_support_ticket(
        session,
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
    )

    notification = create_notification(
        session,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.SMS,
        message="Your ticket has been created.",
    )

    assert notification.notification_id is not None
    session.expire_all()

    fetched = session.get(Notification, notification.notification_id)
    assert fetched.support_ticket_id == ticket.support_ticket_id
    assert fetched.customer_id == customer.customer_id
    assert fetched.notification_type == PreferredNotificationMethod.SMS
    assert fetched.message == "Your ticket has been created."
    assert fetched.sent_at is None


def test_support_ticket_one_to_many_notification_via_repository(session):
    customer, device = _make_customer_and_device(session, "N2")
    chat_session = create_chat_session(
        session, customer_id=customer.customer_id, device_id=device.device_id
    )
    ticket = create_support_ticket(
        session,
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
    )

    n1 = create_notification(
        session,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.EMAIL,
        message="A support agent is reviewing your ticket.",
    )
    n2 = create_notification(
        session,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.PUSH,
        message="Your ticket has been updated.",
    )

    session.expire_all()
    fetched_ticket = session.get(SupportTicket, ticket.support_ticket_id)
    assert {n.notification_id for n in fetched_ticket.notifications} == {
        n1.notification_id,
        n2.notification_id,
    }
    assert {n.notification_type for n in fetched_ticket.notifications} == {
        PreferredNotificationMethod.EMAIL,
        PreferredNotificationMethod.PUSH,
    }
