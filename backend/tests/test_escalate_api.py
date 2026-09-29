"""Tests for `POST /chat/messages/{id}/escalate` -- the customer's explicit
"Yes" to an escalation offer (docs/superpowers/specs/2026-09-30-demo-routes-
and-escalation-design.md).

Fixture pattern copied from test_feedback_api.py: the real production `app`
via `TestClient`, `get_db` overridden to an in-memory SQLite session, and
`ChatSession`/`ChatMessage` rows seeded directly via the ORM.

`app.api.chat.send_escalation_email` is patched for EVERY test (autouse
fixture below), so no test can ever reach a real SMTP server -- even on a
developer machine whose own `.env` sets SMTP_HOST. The one test that
exercises the real email module routes the patch back to it with SMTP
deliberately unconfigured, which raises before any connection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.database import get_db
from app.integrations.email import EmailDeliveryError
from app.integrations.email import send_escalation_email as real_send_escalation_email
from app.main import app
from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Customer,
    Device,
    Notification,
    SupportTicket,
)
from app.models.enums import (
    AccessLevel,
    BatteryStatus,
    ChatMessageRole,
    DeviceStatus,
    PreferredNotificationMethod,
    SessionStatus,
)


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
def db_session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(engine, db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        from fastapi.testclient import TestClient

        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def send_email_mock():
    with patch("app.api.chat.send_escalation_email", return_value=None) as mock_send:
        yield mock_send


def _build_case(db_session: Session, *, tag: str) -> dict:
    customer = Customer(
        full_name=f"Escalate Test Customer {tag}",
        email=f"escalate-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)

    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"ESCALATE-{tag}-DEV-001",
        device_type="obd2",
        battery_status=BatteryStatus.OK,
        device_status=DeviceStatus.ACTIVE,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)

    chat_session = ChatSession(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=SessionStatus.ACTIVE,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    user_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        role=ChatMessageRole.USER,
        content=f"{tag}'s question",
    )
    db_session.add(user_message)
    db_session.commit()
    assistant_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content=f"{tag}'s answer",
    )
    db_session.add(assistant_message)
    db_session.commit()
    db_session.refresh(user_message)
    db_session.refresh(assistant_message)

    token = create_access_token(subject=customer.customer_id, role="customer")
    return {
        "customer": customer,
        "device": device,
        "chat_session": chat_session,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
def case_a(db_session):
    return _build_case(db_session, tag="A")


@pytest.fixture()
def case_b(db_session):
    return _build_case(db_session, tag="B")


@pytest.fixture()
def support_agent_headers(db_session):
    from app.models.support_agent import SupportAgent

    agent = SupportAgent(
        full_name="Escalate Test Support Agent",
        email="escalate-support-agent@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    token = create_access_token(
        subject=agent.support_agent_id, role="support_agent", access_level="tier_2"
    )
    return {"Authorization": f"Bearer {token}"}


def _escalate(client, message_id: int, headers: dict | None):
    return client.post(f"/chat/messages/{message_id}/escalate", headers=headers or {})


def _tickets(db_session, case) -> list[SupportTicket]:
    db_session.expire_all()
    return (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=case["chat_session"].chat_session_id)
        .all()
    )


# ---------------------------------------------------------------------------
# Auth + ownership (mirrors test_feedback_api.py exactly)
# ---------------------------------------------------------------------------


def test_escalate_requires_auth(client, case_a):
    response = _escalate(client, case_a["assistant_message"].chat_message_id, None)
    assert response.status_code == 401


def test_customer_cannot_escalate_another_customers_message(
    client, db_session, case_a, case_b, send_email_mock
):
    response = _escalate(client, case_b["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 404
    assert _tickets(db_session, case_b) == []
    send_email_mock.assert_not_called()


def test_escalate_on_missing_message_returns_404(client, case_a, send_email_mock):
    response = _escalate(client, 999999, case_a["headers"])

    assert response.status_code == 404
    send_email_mock.assert_not_called()


def test_escalate_on_a_user_message_is_rejected(client, db_session, case_a, send_email_mock):
    response = _escalate(client, case_a["user_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 400
    assert _tickets(db_session, case_a) == []
    send_email_mock.assert_not_called()


def test_support_agent_can_escalate_any_customers_message(
    client, db_session, case_a, support_agent_headers
):
    response = _escalate(
        client, case_a["assistant_message"].chat_message_id, support_agent_headers
    )

    assert response.status_code == 200
    assert len(_tickets(db_session, case_a)) == 1


# ---------------------------------------------------------------------------
# Happy path: ticket + in-app notification, then the email
# ---------------------------------------------------------------------------


def test_escalate_creates_one_ticket_and_one_in_app_notification(client, db_session, case_a):
    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"support_ticket_id", "email_sent"}
    assert body["email_sent"] is True

    tickets = _tickets(db_session, case_a)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.support_ticket_id == body["support_ticket_id"]
    assert ticket.customer_id == case_a["customer"].customer_id
    assert ticket.device_id == case_a["device"].device_id
    assert "A's question" in ticket.description
    assert "A's answer" in ticket.description

    notifications = (
        db_session.query(Notification).filter_by(support_ticket_id=ticket.support_ticket_id).all()
    )
    assert len(notifications) == 1
    assert notifications[0].notification_type == PreferredNotificationMethod.IN_APP


def test_email_carries_the_customer_question_answer_and_ticket_id(
    client, case_a, send_email_mock
):
    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    send_email_mock.assert_called_once_with(
        customer_name="Escalate Test Customer A",
        customer_email="escalate-customer-a@example.test",
        question="A's question",
        answer="A's answer",
        support_ticket_id=response.json()["support_ticket_id"],
    )


def test_question_is_the_user_message_immediately_before_the_escalated_answer(
    client, db_session, case_a, send_email_mock
):
    later_question = ChatMessage(
        chat_session_id=case_a["chat_session"].chat_session_id,
        role=ChatMessageRole.USER,
        content="A's later question",
    )
    db_session.add(later_question)
    db_session.commit()
    later_answer = ChatMessage(
        chat_session_id=case_a["chat_session"].chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content="A's later answer",
    )
    db_session.add(later_answer)
    db_session.commit()
    db_session.refresh(later_answer)

    _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])
    _escalate(client, later_answer.chat_message_id, case_a["headers"])

    first_call, second_call = send_email_mock.call_args_list
    assert first_call.kwargs["question"] == "A's question"
    assert second_call.kwargs["question"] == "A's later question"
    assert second_call.kwargs["answer"] == "A's later answer"


def test_email_is_sent_only_after_the_ticket_is_committed(
    client, db_session, case_a, send_email_mock
):
    """Ordering guarantee behind the two failure domains: by the time the
    email is attempted, the ticket must already be durable -- the session
    has no open transaction left to roll back."""
    observed: dict[str, bool] = {}

    def _record_transaction_state(**kwargs):
        observed["in_transaction"] = db_session.in_transaction()

    send_email_mock.side_effect = _record_transaction_state

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    assert observed == {"in_transaction": False}


# ---------------------------------------------------------------------------
# Two failure domains: a failed email never loses the ticket, never 5xx
# ---------------------------------------------------------------------------


def test_ticket_is_created_even_when_the_email_raises(client, db_session, case_a, send_email_mock):
    send_email_mock.side_effect = EmailDeliveryError(
        "Escalation email could not be sent: Connection refused"
    )

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["email_sent"] is False
    tickets = _tickets(db_session, case_a)
    assert len(tickets) == 1
    assert tickets[0].support_ticket_id == body["support_ticket_id"]


def test_an_unexpected_email_error_still_degrades_to_email_not_sent(
    client, db_session, case_a, send_email_mock
):
    send_email_mock.side_effect = RuntimeError("something nobody anticipated")

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    assert response.json()["email_sent"] is False
    assert len(_tickets(db_session, case_a)) == 1


def test_unconfigured_smtp_degrades_to_ticket_created_email_not_sent(
    client, db_session, case_a, send_email_mock, monkeypatch
):
    """End-to-end through the REAL email module with SMTP_HOST empty: it
    raises EmailNotConfiguredError before connecting, and the endpoint
    still returns the committed ticket."""
    monkeypatch.setattr(settings, "smtp_host", "")
    send_email_mock.side_effect = real_send_escalation_email

    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        response = _escalate(
            client, case_a["assistant_message"].chat_message_id, case_a["headers"]
        )

    mock_smtp_cls.assert_not_called()
    assert response.status_code == 200
    assert response.json()["email_sent"] is False
    assert len(_tickets(db_session, case_a)) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_click_creates_one_ticket_not_two(client, db_session, case_a, send_email_mock):
    first = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])
    second = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["support_ticket_id"] == first.json()["support_ticket_id"]

    tickets = _tickets(db_session, case_a)
    assert len(tickets) == 1
    assert (
        db_session.query(Notification)
        .filter_by(support_ticket_id=tickets[0].support_ticket_id)
        .count()
        == 1
    )
    # Documented policy: every successful "Yes" attempts the email, even on
    # a reused ticket -- never report email_sent=false for a mail that was
    # never tried. The widget disables "Yes" while a request is in flight.
    assert send_email_mock.call_count == 2


def test_escalate_reuses_a_ticket_created_earlier_by_a_thumbs_down(client, db_session, case_a):
    feedback = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=case_a["headers"],
    )
    assert feedback.status_code == 200

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    assert response.json()["support_ticket_id"] == feedback.json()["support_ticket_id"]
    assert len(_tickets(db_session, case_a)) == 1
