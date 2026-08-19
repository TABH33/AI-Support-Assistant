"""Tests for `app.ai.escalation` (Task 14).

Same in-memory SQLite fixture pattern as `test_chat_repository.py` (Task 8):
construct via the repository functions, commit, `expire_all()`, then
re-fetch fresh copies via `session.get()` / real queries to prove the
escalation flow genuinely round-trips through the DB, not just that the
in-memory return values look right.

Covers:
  * High-confidence `ChatAnswer` passes through unchanged, with NO
    `SupportTicket` created for that session (verified via a real query).
  * Low-confidence `ChatAnswer` creates EXACTLY ONE `SupportTicket` and ONE
    `Notification`, and returns the exact `FALLBACK_TEXT` constant
    (re-fetched from the DB, not just inspected off the return value).
  * The threshold comparison direction: exactly-at-threshold does NOT
    escalate ("below threshold" escalates, not "at or below").
  * Escalating twice for the same session raises `IntegrityError` (Task 8's
    DB-level ChatSession 1 -> 0..1 SupportTicket constraint), not silently
    creating a second ticket.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer
from app.ai.escalation import EscalationResult, handle_answer
from app.config import settings
from app.models import Base, ChatSession, Customer, Device, Notification, SupportTicket
from app.models.enums import DeviceStatus, PreferredNotificationMethod
from app.repositories.chat import create_chat_session


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
        full_name=f"Escalation Customer {suffix}",
        email=f"escalation{suffix}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"SN-ESC-{suffix}",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()
    return customer, device


def _make_chat_session(session, suffix: str) -> ChatSession:
    customer, device = _make_customer_and_device(session, suffix)
    return create_chat_session(session, customer_id=customer.customer_id, device_id=device.device_id)


def _tickets_for_session(session, chat_session_id: int) -> list[SupportTicket]:
    return list(
        session.execute(
            select(SupportTicket).where(SupportTicket.chat_session_id == chat_session_id)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# High confidence: pass-through, no ticket
# ---------------------------------------------------------------------------


def test_high_confidence_answer_passes_through_unchanged_with_no_ticket(session):
    chat_session = _make_chat_session(session, "HC1")
    answer = ChatAnswer(text="Your device battery is at 80%.", confidence=0.9)
    assert answer.confidence >= settings.escalation_confidence_threshold

    result = handle_answer(session, chat_session.chat_session_id, answer)

    assert isinstance(result, EscalationResult)
    assert result.escalated is False
    assert result.text == "Your device battery is at 80%."
    assert result.support_ticket_id is None

    # Verify via a real DB query -- not just the return value -- that no
    # SupportTicket row exists for this session.
    session.expire_all()
    tickets = _tickets_for_session(session, chat_session.chat_session_id)
    assert tickets == []


def test_confidence_exactly_at_threshold_does_not_escalate(session):
    """'below threshold' escalates; exactly-at-threshold must NOT (direction check)."""
    chat_session = _make_chat_session(session, "HC2")
    answer = ChatAnswer(text="Exactly at the line.", confidence=settings.escalation_confidence_threshold)

    result = handle_answer(session, chat_session.chat_session_id, answer)

    assert result.escalated is False
    assert result.text == "Exactly at the line."
    session.expire_all()
    assert _tickets_for_session(session, chat_session.chat_session_id) == []


# ---------------------------------------------------------------------------
# Low confidence: escalate, exactly one ticket + notification, fallback text
# ---------------------------------------------------------------------------


def test_low_confidence_answer_escalates_creates_one_ticket_and_notification(session):
    chat_session = _make_chat_session(session, "LC1")
    raw_text = "I think maybe the device is possibly offline?"
    answer = ChatAnswer(text=raw_text, confidence=0.1)
    assert answer.confidence < settings.escalation_confidence_threshold

    result = handle_answer(session, chat_session.chat_session_id, answer)

    assert result.escalated is True
    assert result.text == FALLBACK_TEXT
    assert result.text is FALLBACK_TEXT  # exact constant reused, not retyped
    assert result.support_ticket_id is not None

    # Re-fetch from the DB (not the in-memory return value) to prove a real
    # round-trip: exactly one SupportTicket for this session.
    session.expire_all()
    tickets = _tickets_for_session(session, chat_session.chat_session_id)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.support_ticket_id == result.support_ticket_id
    assert ticket.customer_id == chat_session.customer_id
    assert ticket.device_id == chat_session.device_id

    # Exactly one Notification, linked to that ticket.
    notifications = list(
        session.execute(
            select(Notification).where(Notification.support_ticket_id == ticket.support_ticket_id)
        )
        .scalars()
        .all()
    )
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.customer_id == chat_session.customer_id
    assert notification.message  # a real, non-empty message was recorded


def test_low_confidence_answer_returns_fallback_text_not_raw_llm_text(session):
    chat_session = _make_chat_session(session, "LC2")
    answer = ChatAnswer(text="some unreliable raw LLM guess", confidence=0.0)

    result = handle_answer(session, chat_session.chat_session_id, answer)

    assert result.text == FALLBACK_TEXT
    assert "unreliable raw LLM guess" not in result.text


def test_unknown_chat_session_raises_value_error(session):
    answer = ChatAnswer(text="doesn't matter", confidence=0.0)

    with pytest.raises(ValueError):
        handle_answer(session, 999_999, answer)


def test_escalating_twice_for_same_session_raises_integrity_error(session):
    """Task 8's DB-level ChatSession 1 -> 0..1 SupportTicket constraint applies
    here too: a second escalation for the same session must not silently
    create a second ticket."""
    chat_session = _make_chat_session(session, "LC3")
    first_answer = ChatAnswer(text="first low-confidence answer", confidence=0.0)
    handle_answer(session, chat_session.chat_session_id, first_answer)

    second_answer = ChatAnswer(text="second low-confidence answer", confidence=0.0)
    with pytest.raises(IntegrityError):
        handle_answer(session, chat_session.chat_session_id, second_answer)
    session.rollback()

    session.expire_all()
    tickets = _tickets_for_session(session, chat_session.chat_session_id)
    assert len(tickets) == 1
