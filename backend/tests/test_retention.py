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
from app.models import Base, ChatSession, Customer, Device, Notification, SupportTicket
from app.models.enums import DeviceStatus, PreferredNotificationMethod, SessionStatus, TicketStatus

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
# Directly-owned data / ticket-status gate: an expired session with a
# SupportTicket is preserved ONLY while the ticket's lifecycle is still
# active (OPEN/IN_PROGRESS). Once the ticket reaches a terminal status
# (RESOLVED/CLOSED), the session becomes purgeable again like any other
# expired session -- and because ChatSession.support_ticket cascades,
# purging it also deletes the ticket and its notifications.
# ---------------------------------------------------------------------------


def _make_expired_session_with_ticket(
    session, suffix: str, ticket_status: TicketStatus
) -> tuple[ChatSession, SupportTicket]:
    chat_session = _make_chat_session(
        session,
        suffix,
        start_time=FIXED_NOW - timedelta(hours=2),
        end_time=CUTOFF - timedelta(minutes=1),  # well past the cutoff
        session_status=SessionStatus.ENDED,
    )
    ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=chat_session.customer_id,
        device_id=chat_session.device_id,
        ticket_status=ticket_status,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return chat_session, ticket


def test_expired_session_with_open_ticket_is_preserved(session):
    """A ticket still in OPEN status is actively unresolved work -- the
    session it originated from must survive alongside it, even once the
    session itself is well past its own timeout."""
    chat_session, ticket = _make_expired_session_with_ticket(session, "TICK-OPEN", TicketStatus.OPEN)

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 0
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is not None
    assert session.get(SupportTicket, ticket.support_ticket_id) is not None


def test_expired_session_with_in_progress_ticket_is_preserved(session):
    """Same guard as OPEN, for IN_PROGRESS -- both are non-terminal."""
    chat_session, ticket = _make_expired_session_with_ticket(
        session, "TICK-PROG", TicketStatus.IN_PROGRESS
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 0
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is not None
    assert session.get(SupportTicket, ticket.support_ticket_id) is not None


def test_expired_session_with_resolved_ticket_is_purged(session):
    """A RESOLVED ticket is terminal: the exception no longer applies, so
    the expired session (and, via cascade, its ticket and the ticket's
    notifications) IS purged like any other expired session."""
    chat_session, ticket = _make_expired_session_with_ticket(
        session, "TICK-RES", TicketStatus.RESOLVED
    )
    notification = Notification(
        support_ticket_id=ticket.support_ticket_id,
        customer_id=chat_session.customer_id,
        notification_type=PreferredNotificationMethod.EMAIL,
        message="Your ticket has been resolved.",
    )
    session.add(notification)
    session.commit()

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is None
    assert session.get(SupportTicket, ticket.support_ticket_id) is None
    assert session.get(Notification, notification.notification_id) is None


def test_expired_session_with_closed_ticket_is_purged(session):
    """Same terminal-status behavior as RESOLVED, for CLOSED."""
    chat_session, ticket = _make_expired_session_with_ticket(
        session, "TICK-CLOSED", TicketStatus.CLOSED
    )

    purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)

    assert purged == 1
    session.expire_all()
    assert session.get(ChatSession, chat_session.chat_session_id) is None
    assert session.get(SupportTicket, ticket.support_ticket_id) is None


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


# ---------------------------------------------------------------------------
# Query efficiency: SQL-level filtering, no N+1 on support_ticket access
# ---------------------------------------------------------------------------


def test_purge_filters_at_sql_level_and_avoids_n_plus_one(session, engine):
    """`purge_expired_sessions` must not load the whole `chat_sessions` table
    and must not issue one extra query per row to resolve `support_ticket`.

    Sets up: two already-purgeable expired sessions (no ticket), one
    expired session with a non-terminal (OPEN) ticket that must be
    preserved, and one untouched active session -- four rows total, only
    two of which should even be candidates, and only two of which should
    ultimately be deleted. Counts every SELECT SQLite actually executes
    during the call: it must be exactly one (the single candidate query,
    with `support_ticket` eager-loaded via `joinedload`), never four-plus
    (one full-table scan) or growing with the number of tickets (N+1).
    """
    expired_no_ticket_1 = _make_chat_session(
        session,
        "EFF1",
        start_time=FIXED_NOW - timedelta(hours=2),
        end_time=CUTOFF - timedelta(minutes=5),
        session_status=SessionStatus.ENDED,
    )
    expired_no_ticket_2 = _make_chat_session(
        session,
        "EFF2",
        start_time=FIXED_NOW - timedelta(hours=2),
        end_time=CUTOFF - timedelta(minutes=10),
        session_status=SessionStatus.ENDED,
    )
    expired_with_open_ticket, _ticket = _make_expired_session_with_ticket(
        session, "EFF3", TicketStatus.OPEN
    )
    active = _make_chat_session(
        session,
        "EFF4",
        start_time=FIXED_NOW - timedelta(minutes=1),
        end_time=None,
        session_status=SessionStatus.ACTIVE,
    )

    select_statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.strip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        purged = purge_expired_sessions(session, TIMEOUT_MINUTES, now=FIXED_NOW)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert purged == 2
    assert len(select_statements) == 1, (
        f"Expected exactly one SELECT (candidate query with eager-loaded "
        f"support_ticket), got {len(select_statements)}: {select_statements}"
    )

    session.expire_all()
    assert session.get(ChatSession, expired_no_ticket_1.chat_session_id) is None
    assert session.get(ChatSession, expired_no_ticket_2.chat_session_id) is None
    assert session.get(ChatSession, expired_with_open_ticket.chat_session_id) is not None
    assert session.get(ChatSession, active.chat_session_id) is not None
