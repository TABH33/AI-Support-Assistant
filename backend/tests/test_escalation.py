"""Tests for `app.ai.escalation` (Task 14, reworked for opt-in escalation --
docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).

Two halves, matching the module's two functions:
  * `handle_answer` is now a pure decision: high confidence passes the LLM
    text through; low confidence swaps in the exact `FALLBACK_TEXT` and sets
    `escalation_offered=True`. It never creates a ticket -- it no longer
    even takes a `Session` -- and never sets `escalated=True`.
  * `get_or_create_escalation_ticket` (called only by the explicit
    `POST /chat/messages/{id}/escalate` endpoint) is tested against a real
    in-memory SQLite round-trip, same fixture pattern as
    `test_chat_repository.py`: exactly one ticket + one in-app notification,
    reuse instead of `IntegrityError` on a second call, and the
    SAVEPOINT-scoped recovery for the concurrent race (final-review Fix 6),
    which must not roll back unrelated work staged earlier in the same
    outer transaction.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer
from app.ai.escalation import EscalationResult, get_or_create_escalation_ticket, handle_answer
from app.config import settings
from app.models import Base, ChatSession, Customer, Device, Notification, SupportTicket
from app.models.enums import DeviceStatus, PreferredNotificationMethod
from app.repositories.chat import create_chat_session, create_support_ticket


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


def _notifications_for_ticket(session, support_ticket_id: int) -> list[Notification]:
    return list(
        session.execute(
            select(Notification).where(Notification.support_ticket_id == support_ticket_id)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# handle_answer: a pure offer/no-offer decision
# ---------------------------------------------------------------------------


def test_high_confidence_answer_passes_through_and_offers_nothing():
    answer = ChatAnswer(text="Your device battery is at 80%.", confidence=0.9)
    assert answer.confidence >= settings.escalation_confidence_threshold

    result = handle_answer(answer)

    assert isinstance(result, EscalationResult)
    assert result.text == "Your device battery is at 80%."
    assert result.escalated is False
    assert result.escalation_offered is False
    assert result.support_ticket_id is None


def test_confidence_exactly_at_threshold_does_not_offer_escalation():
    """'below threshold' offers; exactly-at-threshold must NOT (direction check)."""
    answer = ChatAnswer(text="Exactly at the line.", confidence=settings.escalation_confidence_threshold)

    result = handle_answer(answer)

    assert result.escalation_offered is False
    assert result.text == "Exactly at the line."


def test_low_confidence_answer_offers_escalation_with_the_exact_fallback_text():
    answer = ChatAnswer(text="I think maybe the device is possibly offline?", confidence=0.1)
    assert answer.confidence < settings.escalation_confidence_threshold

    result = handle_answer(answer)

    assert result.text is FALLBACK_TEXT  # exact constant reused, not retyped
    assert "possibly offline" not in result.text
    assert result.escalation_offered is True
    # Offered, NOT escalated: nothing exists yet until the customer says yes.
    assert result.escalated is False
    assert result.support_ticket_id is None


def test_handle_answer_cannot_create_database_rows():
    """Opt-in guard: `handle_answer` takes no `Session`, so it has no way to
    create a ticket implicitly. Re-adding a `db` parameter would be the
    first step back toward silent auto-escalation -- this test should fail
    loudly if that happens."""
    assert list(inspect.signature(handle_answer).parameters) == ["chat_answer"]


# ---------------------------------------------------------------------------
# get_or_create_escalation_ticket: the explicit-confirm path's ticket helper
# ---------------------------------------------------------------------------


def test_creates_exactly_one_ticket_and_one_in_app_notification(session):
    chat_session = _make_chat_session(session, "GC1")

    ticket = get_or_create_escalation_ticket(
        session,
        chat_session,
        description="Customer question: why?\n\nAssistant answer: fallback",
    )
    session.commit()

    session.expire_all()
    tickets = _tickets_for_session(session, chat_session.chat_session_id)
    assert len(tickets) == 1
    assert tickets[0].support_ticket_id == ticket.support_ticket_id
    assert tickets[0].customer_id == chat_session.customer_id
    assert tickets[0].device_id == chat_session.device_id
    assert tickets[0].subject
    assert tickets[0].description == "Customer question: why?\n\nAssistant answer: fallback"

    notifications = _notifications_for_ticket(session, ticket.support_ticket_id)
    assert len(notifications) == 1
    assert notifications[0].customer_id == chat_session.customer_id
    assert notifications[0].notification_type == PreferredNotificationMethod.IN_APP
    assert notifications[0].message


def test_second_call_reuses_the_ticket_without_a_second_notification(session):
    """Double-click safety: `ChatSession 1 -> 0..1 SupportTicket` is a DB
    UNIQUE constraint, so a second call must select-and-reuse, not raise
    `IntegrityError` -- and must not notify the customer twice."""
    chat_session = _make_chat_session(session, "GC2")

    first = get_or_create_escalation_ticket(session, chat_session, description="first")
    second = get_or_create_escalation_ticket(session, chat_session, description="second")
    session.commit()

    assert second.support_ticket_id == first.support_ticket_id
    session.expire_all()
    assert len(_tickets_for_session(session, chat_session.chat_session_id)) == 1
    assert len(_notifications_for_ticket(session, first.support_ticket_id)) == 1


def test_concurrent_ticket_race_recovers_via_savepoint_without_losing_other_staged_work(
    session, monkeypatch
):
    """Defense-in-depth path (final-review Fix 6 interaction): simulates the
    genuinely concurrent race two simultaneous "Yes" clicks for the same
    session could hit -- the initial SELECT misses a ticket a "concurrent"
    insert already created, so `create_support_ticket`'s INSERT hits the
    real UNIQUE constraint.

    `get_or_create_escalation_ticket` must recover via its except-block
    re-select (finding the real, already-flushed ticket) AND must NOT roll
    back unrelated work staged earlier in the same outer transaction --
    proving the `db.begin_nested()` SAVEPOINT scopes the rollback to only
    the failed insert, not the whole transaction.
    """
    chat_session = _make_chat_session(session, "RACE1")
    # Stand-in for other work staged earlier in the SAME outer transaction.
    other_chat_session = _make_chat_session(session, "RACE1-OTHER")

    # Stand-in for "a concurrent request already won the race".
    winning_ticket = create_support_ticket(
        session,
        chat_session_id=chat_session.chat_session_id,
        customer_id=chat_session.customer_id,
        device_id=chat_session.device_id,
        subject="Concurrent request's ticket",
    )

    # Force exactly the NEXT `.one_or_none()` call -- the helper's own
    # initial select-first check -- to report "nothing found".
    original_one_or_none = SAQuery.one_or_none
    state = {"missed_once": False}

    def _miss_once(self):
        if not state["missed_once"]:
            state["missed_once"] = True
            return None
        return original_one_or_none(self)

    monkeypatch.setattr(SAQuery, "one_or_none", _miss_once)

    ticket = get_or_create_escalation_ticket(
        session, chat_session, description="a low-confidence answer"
    )

    assert ticket.support_ticket_id == winning_ticket.support_ticket_id

    session.expire_all()
    assert session.get(ChatSession, other_chat_session.chat_session_id) is not None
