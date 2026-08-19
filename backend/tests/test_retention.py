"""Tests for `app.jobs.retention` (Task 9).

Runs against an in-memory SQLite database (with `PRAGMA foreign_keys=ON`),
same fixture pattern as `test_models.py` (Task 4) and `test_chat_repository.py`
(Task 8).

Time is injected via `purge_expired_sessions(..., now=...)` rather than
relying on the real wall clock, so the boundary-at-exactly-the-timeout case
can be tested deterministically instead of racing real time.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.jobs.retention import purge_expired_sessions
from app.models import Base, ChatSession, Customer, Device, SupportTicket
from app.models.enums import DeviceStatus, PreferredNotificationMethod, SessionStatus

TIMEOUT_MINUTES = 30
FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CUTOFF = FIXED_NOW - timedelta(minutes=TIMEOUT_MINUTES)  # 11:30:00


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


def _make_customer_and_device(session, suffix: str) -> tuple[Customer, Device]:
    customer = Customer(
        full_name=f"Retention Customer {suffix}",
        email=f"retention{suffix}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"SN-RET-{suffix}",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()
    return customer, device


def _make_chat_session(
    session,
    suffix: str,
    *,
    start_time: datetime,
    end_time: datetime | None,
    session_status: SessionStatus,
) -> ChatSession:
    customer, device = _make_customer_and_device(session, suffix)
    chat_session = ChatSession(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=session_status,
        start_time=start_time,
        end_time=end_time,
    )
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return chat_session


# ---------------------------------------------------------------------------
# Expired session (ended well past the timeout) is purged
# ---------------------------------------------------------------------------


def test_expired_ended_session_is_purged(session):
    chat_session = _make_chat_session(
        session,
        "EXP1",
        start_time=FIXED_NOW - timedelta(hours=2),
        end_time=CUTOFF - timedelta(minutes=1),  # 1 min past the cutoff
        session_status=SessionStatus.ENDED,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is None


def test_expired_never_ended_session_uses_start_time_and_is_purged(session):
    """A session that was never explicitly ended (end_time is None) expires
    `timeout_minutes` after start_time, per the brief's "or since StartTime
    if never ended" clause."""
    chat_session = _make_chat_session(
        session,
        "EXP2",
        start_time=CUTOFF - timedelta(minutes=1),  # 1 min past the cutoff
        end_time=None,
        session_status=SessionStatus.ACTIVE,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is None


# ---------------------------------------------------------------------------
# Active (non-expired) session is untouched
# ---------------------------------------------------------------------------


def test_active_recent_session_is_untouched(session):
    chat_session = _make_chat_session(
        session,
        "ACT1",
        start_time=FIXED_NOW - timedelta(minutes=5),
        end_time=None,
        session_status=SessionStatus.ACTIVE,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 0
    session.expire_all()
    fetched = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched is not None
    assert fetched.session_status == SessionStatus.ACTIVE


def test_recently_ended_session_within_timeout_is_untouched(session):
    chat_session = _make_chat_session(
        session,
        "ACT2",
        start_time=FIXED_NOW - timedelta(minutes=10),
        end_time=CUTOFF + timedelta(minutes=1),  # 1 min inside the window
        session_status=SessionStatus.ENDED,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 0
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is not None


# ---------------------------------------------------------------------------
# Boundary case: exactly at the timeout
# ---------------------------------------------------------------------------


def test_session_exactly_at_timeout_boundary_is_purged(session):
    """Deliberate boundary decision: elapsed time exactly equal to
    `timeout_minutes` counts as expired (a `<=` comparison against the
    cutoff, not `<`). end_time is set to exactly `CUTOFF`, i.e. exactly
    `TIMEOUT_MINUTES` before `FIXED_NOW`."""
    chat_session = _make_chat_session(
        session,
        "BOUND1",
        start_time=CUTOFF - timedelta(minutes=10),
        end_time=CUTOFF,
        session_status=SessionStatus.ENDED,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is None


def test_session_one_second_inside_timeout_boundary_is_untouched(session):
    """One second on the not-yet-expired side of the same boundary must
    survive, proving the `<=` cutoff is precise rather than off by a wide
    margin in either direction."""
    chat_session = _make_chat_session(
        session,
        "BOUND2",
        start_time=CUTOFF - timedelta(minutes=10),
        end_time=CUTOFF + timedelta(seconds=1),
        session_status=SessionStatus.ENDED,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 0
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is not None


# ---------------------------------------------------------------------------
# Directly-owned data / compliance guard: an expired session with a
# SupportTicket is NOT purged (neither the session nor the ticket)
# ---------------------------------------------------------------------------


def test_expired_session_with_support_ticket_is_preserved(session):
    """A ChatSession that escalated to a SupportTicket must survive session
    retention even once expired: the ticket is a separate compliance/audit
    record (see `purge_expired_sessions`'s docstring), and deleting the
    session would either cascade-delete the ticket (violating that
    requirement) or violate the ticket's NOT-NULL FK to the session."""
    chat_session = _make_chat_session(
        session,
        "TICK1",
        start_time=FIXED_NOW - timedelta(hours=2),
        end_time=CUTOFF - timedelta(minutes=1),  # well past the cutoff
        session_status=SessionStatus.ENDED,
    )
    ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=chat_session.customer_id,
        device_id=chat_session.device_id,
    )
    session.add(ticket)
    session.commit()

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 0
    session.expire_all()
    fetched_session = session.get(ChatSession, chat_session.chat_session_id)
    assert fetched_session is not None
    assert session.get(SupportTicket, ticket.support_ticket_id) is not None


# ---------------------------------------------------------------------------
# Mixed batch: purges only what should be purged, leaves the rest
# ---------------------------------------------------------------------------


def test_purge_only_deletes_expired_sessions_in_a_mixed_batch(session):
    expired = _make_chat_session(
        session,
        "MIX1",
        start_time=FIXED_NOW - timedelta(hours=2),
        end_time=CUTOFF - timedelta(minutes=5),
        session_status=SessionStatus.ENDED,
    )
    active = _make_chat_session(
        session,
        "MIX2",
        start_time=FIXED_NOW - timedelta(minutes=1),
        end_time=None,
        session_status=SessionStatus.ACTIVE,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, expired.chat_session_id) is None
    assert session.get(ChatSession, active.chat_session_id) is not None


def test_purge_defaults_to_real_wall_clock_when_now_not_supplied(session):
    """`now` is optional -- omitting it (as the `python -m app.jobs.retention`
    entry point does) falls back to the real current time."""
    chat_session = _make_chat_session(
        session,
        "REAL1",
        start_time=datetime.now(timezone.utc) - timedelta(days=1),
        end_time=datetime.now(timezone.utc) - timedelta(hours=1),
        session_status=SessionStatus.ENDED,
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is None
