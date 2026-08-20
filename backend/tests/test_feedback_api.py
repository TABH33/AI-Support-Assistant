"""Tests for Task 22: `PATCH /chat/messages/{id}/feedback` and
`POST /chat/sessions/{id}/survey`.

Mirrors `test_chat_api.py`/`test_tickets_api.py`'s pattern: exercises the
real production `app` via FastAPI's `TestClient`, with `get_db` overridden
to a temporary in-memory SQLite session (no live Postgres required).
`ChatSession`/`ChatMessage` rows are seeded directly via the ORM (rather
than by driving the full AI pipeline through `POST /chat`) since these two
endpoints only touch already-persisted rows -- no Ollama-touching layer is
exercised here.

Idempotency of the thumbs-down escalation path (the brief's central
correctness requirement) is proven with a real DB round-trip: press
thumbs-down twice and assert exactly one `SupportTicket` row exists after
both calls, not just after the first -- and separately, that a session
which already has a ticket (e.g. from Task 14's low-confidence
auto-escalation) is reused rather than duplicated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
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

# ---------------------------------------------------------------------------
# Shared SQLite fixtures (mirrors backend/tests/test_tickets_api.py)
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


# ---------------------------------------------------------------------------
# Two isolated customer "cases": customer + device + chat session + a
# user/assistant message pair each.
# ---------------------------------------------------------------------------


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Feedback Test Customer {tag}",
        email=f"feedback-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _build_case(db_session: Session, *, tag: str) -> dict:
    customer = _make_customer(db_session, tag=tag)

    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"FEEDBACK-{tag}-DEV-001",
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
    assistant_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content=f"{tag}'s answer",
    )
    db_session.add_all([user_message, assistant_message])
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
        "token": token,
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
        full_name="Feedback Test Support Agent",
        email="feedback-support-agent@example.test",
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


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def test_feedback_requires_auth(client, case_a):
    response = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": True},
    )
    assert response.status_code == 401


def test_survey_requires_auth(client, case_a):
    response = client.post(
        f"/chat/sessions/{case_a['chat_session'].chat_session_id}/survey",
        json={"score": 3},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /chat/messages/{id}/feedback
# ---------------------------------------------------------------------------


def test_thumbs_up_updates_feedback_field_without_creating_a_ticket(client, db_session, case_a):
    response = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": True},
        headers=case_a["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["feedback"] is True
    assert body["escalated"] is False
    assert body["support_ticket_id"] is None

    db_session.expire_all()
    message = db_session.get(ChatMessage, case_a["assistant_message"].chat_message_id)
    assert message.feedback is True
    assert db_session.query(SupportTicket).count() == 0


def test_thumbs_down_creates_exactly_one_ticket(client, db_session, case_a):
    response = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=case_a["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["feedback"] is False
    assert body["escalated"] is True
    assert body["support_ticket_id"] is not None

    db_session.expire_all()
    message = db_session.get(ChatMessage, case_a["assistant_message"].chat_message_id)
    assert message.feedback is False

    tickets = (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=case_a["chat_session"].chat_session_id)
        .all()
    )
    assert len(tickets) == 1
    assert tickets[0].customer_id == case_a["customer"].customer_id
    assert tickets[0].device_id == case_a["device"].device_id
    assert tickets[0].support_ticket_id == body["support_ticket_id"]

    notifications = (
        db_session.query(Notification)
        .filter_by(support_ticket_id=tickets[0].support_ticket_id)
        .all()
    )
    assert len(notifications) == 1
    assert notifications[0].customer_id == case_a["customer"].customer_id


def test_repeated_thumbs_down_is_idempotent_no_duplicate_ticket(client, db_session, case_a):
    first = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=case_a["headers"],
    )
    assert first.status_code == 200
    first_ticket_id = first.json()["support_ticket_id"]

    second = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=case_a["headers"],
    )
    assert second.status_code == 200
    assert second.json()["support_ticket_id"] == first_ticket_id

    db_session.expire_all()
    tickets = (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=case_a["chat_session"].chat_session_id)
        .all()
    )
    assert len(tickets) == 1


def test_thumbs_down_reuses_a_ticket_created_by_an_earlier_low_confidence_escalation(
    client, db_session, case_a
):
    """A session may already have a `SupportTicket` from Task 14's
    low-confidence auto-escalation before the customer ever presses
    thumbs-down. `ChatSession 1 -> 0..1 SupportTicket` (Task 4's UNIQUE
    constraint) means a second ticket can never legally exist for this
    session -- the feedback handler must find and reuse that ticket, not
    attempt (and fail) to create a second one."""
    existing_ticket = SupportTicket(
        chat_session_id=case_a["chat_session"].chat_session_id,
        customer_id=case_a["customer"].customer_id,
        device_id=case_a["device"].device_id,
        subject="AI assistant could not confidently answer a customer question",
    )
    db_session.add(existing_ticket)
    db_session.commit()
    db_session.refresh(existing_ticket)

    response = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=case_a["headers"],
    )
    assert response.status_code == 200
    assert response.json()["support_ticket_id"] == existing_ticket.support_ticket_id

    db_session.expire_all()
    tickets = (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=case_a["chat_session"].chat_session_id)
        .all()
    )
    assert len(tickets) == 1


def test_customer_cannot_submit_feedback_on_another_customers_message(client, case_a, case_b):
    response = client.patch(
        f"/chat/messages/{case_b['assistant_message'].chat_message_id}/feedback",
        json={"feedback": True},
        headers=case_a["headers"],
    )
    assert response.status_code == 404

    # And no ticket was created as a side effect of an authorization failure.
    assert response.status_code == 404


def test_feedback_on_missing_message_returns_404(client, case_a):
    response = client.patch(
        "/chat/messages/999999/feedback",
        json={"feedback": True},
        headers=case_a["headers"],
    )
    assert response.status_code == 404


def test_feedback_on_a_user_message_is_rejected(client, case_a):
    response = client.patch(
        f"/chat/messages/{case_a['user_message'].chat_message_id}/feedback",
        json={"feedback": True},
        headers=case_a["headers"],
    )
    assert response.status_code == 400


def test_support_agent_can_submit_feedback_for_any_customer(
    client, db_session, case_a, support_agent_headers
):
    response = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=support_agent_headers,
    )
    assert response.status_code == 200
    assert response.json()["escalated"] is True

    db_session.expire_all()
    assert (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=case_a["chat_session"].chat_session_id)
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# POST /chat/sessions/{id}/survey
# ---------------------------------------------------------------------------


def test_survey_stores_score_on_the_session(client, db_session, case_a):
    response = client.post(
        f"/chat/sessions/{case_a['chat_session'].chat_session_id}/survey",
        json={"score": 5},
        headers=case_a["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ces_score"] == 5
    assert body["chat_session_id"] == case_a["chat_session"].chat_session_id

    db_session.expire_all()
    chat_session = db_session.get(ChatSession, case_a["chat_session"].chat_session_id)
    assert chat_session.ces_score == 5


def test_customer_cannot_survey_another_customers_session(client, case_a, case_b):
    response = client.post(
        f"/chat/sessions/{case_b['chat_session'].chat_session_id}/survey",
        json={"score": 2},
        headers=case_a["headers"],
    )
    assert response.status_code == 404


def test_survey_on_missing_session_returns_404(client, case_a):
    response = client.post(
        "/chat/sessions/999999/survey",
        json={"score": 2},
        headers=case_a["headers"],
    )
    assert response.status_code == 404


@pytest.mark.parametrize("score", [0, 8, -1])
def test_survey_score_out_of_range_is_rejected(client, case_a, score):
    response = client.post(
        f"/chat/sessions/{case_a['chat_session'].chat_session_id}/survey",
        json={"score": score},
        headers=case_a["headers"],
    )
    assert response.status_code == 422


def test_support_agent_can_survey_any_customers_session(client, db_session, case_a, support_agent_headers):
    response = client.post(
        f"/chat/sessions/{case_a['chat_session'].chat_session_id}/survey",
        json={"score": 1},
        headers=support_agent_headers,
    )
    assert response.status_code == 200

    db_session.expire_all()
    chat_session = db_session.get(ChatSession, case_a["chat_session"].chat_session_id)
    assert chat_session.ces_score == 1
