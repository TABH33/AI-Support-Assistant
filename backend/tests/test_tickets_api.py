"""Tests for Task 20: `GET /tickets` and `GET /notifications`.

Mirrors `test_telematics_api.py`'s pattern exactly: exercises the *real*
production `app` from `app.main` via FastAPI's `TestClient`, with `get_db`
overridden to a temporary in-memory SQLite session (no live Postgres
required).

Two customers ("A" and "B") are each given their own ticket + notification
so the cross-tenant isolation tests can prove customer A's data is never
visible to customer B (and vice versa) -- not just asserted by inspection of
the route code.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Base,
    ChatSession,
    Customer,
    Device,
    Notification,
    SupportAgent,
    SupportTicket,
)
from app.models.enums import (
    AccessLevel,
    BatteryStatus,
    DeviceStatus,
    Priority,
    SessionStatus,
    PreferredNotificationMethod,
    TicketStatus,
)

# ---------------------------------------------------------------------------
# Shared SQLite fixtures (mirrors backend/tests/test_telematics_api.py)
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
# Two isolated customer "cases": customer + device + chat session + ticket +
# notification each.
# ---------------------------------------------------------------------------


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Ticket Test Customer {tag}",
        email=f"ticket-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture()
def case_a(db_session):
    return _build_case(db_session, tag="A", status_=TicketStatus.OPEN, priority=Priority.HIGH)


@pytest.fixture()
def case_b(db_session):
    return _build_case(
        db_session, tag="B", status_=TicketStatus.IN_PROGRESS, priority=Priority.MEDIUM
    )


def _build_case(
    db_session: Session, *, tag: str, status_: TicketStatus, priority: Priority
) -> dict:
    customer = _make_customer(db_session, tag=tag)

    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"TICKET-{tag}-DEV-001",
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
        session_status=SessionStatus.ENDED,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    ticket = SupportTicket(
        chat_session_id=chat_session.chat_session_id,
        customer_id=customer.customer_id,
        device_id=device.device_id,
        ticket_status=status_,
        priority=priority,
        subject=f"Ticket subject {tag}",
        description=f"Ticket description {tag}",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    notification = Notification(
        support_ticket_id=ticket.support_ticket_id,
        customer_id=customer.customer_id,
        notification_type=PreferredNotificationMethod.EMAIL,
        message=f"Notification message {tag}",
        sent_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(notification)
    db_session.commit()
    db_session.refresh(notification)

    token = create_access_token(subject=customer.customer_id, role="customer")

    return {
        "customer": customer,
        "device": device,
        "chat_session": chat_session,
        "ticket": ticket,
        "notification": notification,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
def support_agent(db_session):
    agent = SupportAgent(
        full_name="Ticket Test Support Agent",
        email="ticket-support-agent@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    token = create_access_token(
        subject=agent.support_agent_id, role="support_agent", access_level="tier_2"
    )
    return {"agent": agent, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


# ---------------------------------------------------------------------------
# Unauthenticated access is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/tickets", "/notifications"])
def test_unauthenticated_request_is_rejected(client, path):
    response = client.get(path)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Happy path: a customer sees their own tickets/notifications
# ---------------------------------------------------------------------------


def test_list_tickets_happy_path(client, case_a):
    response = client.get("/tickets", headers=case_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["support_ticket_id"] == case_a["ticket"].support_ticket_id
    assert body[0]["customer_id"] == case_a["customer"].customer_id
    assert body[0]["ticket_status"] == "open"
    assert body[0]["priority"] == "high"
    assert body[0]["subject"] == "Ticket subject A"


def test_list_notifications_happy_path(client, case_a):
    response = client.get("/notifications", headers=case_a["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["notification_id"] == case_a["notification"].notification_id
    assert body[0]["customer_id"] == case_a["customer"].customer_id
    assert body[0]["support_ticket_id"] == case_a["ticket"].support_ticket_id
    assert body[0]["notification_type"] == "email"
    assert body[0]["message"] == "Notification message A"


# ---------------------------------------------------------------------------
# Two-customer isolation: customer A must never see customer B's data
# ---------------------------------------------------------------------------


def test_customer_cannot_list_another_customers_tickets(client, case_a, case_b):
    response = client.get("/tickets", headers=case_a["headers"])
    assert response.status_code == 200
    ids = [row["support_ticket_id"] for row in response.json()]
    assert case_b["ticket"].support_ticket_id not in ids
    assert ids == [case_a["ticket"].support_ticket_id]


def test_customer_cannot_list_another_customers_notifications(client, case_a, case_b):
    response = client.get("/notifications", headers=case_a["headers"])
    assert response.status_code == 200
    ids = [row["notification_id"] for row in response.json()]
    assert case_b["notification"].notification_id not in ids
    assert ids == [case_a["notification"].notification_id]


def test_customer_supplied_customer_id_filter_on_tickets_is_ignored(client, case_a, case_b):
    """A customer-role caller can't use the ?customer_id= query param to see
    another customer's tickets -- the JWT-derived id always wins (mirrors
    Task 7's equivalent /devices test)."""
    response = client.get(
        "/tickets",
        params={"customer_id": case_b["customer"].customer_id},
        headers=case_a["headers"],
    )
    assert response.status_code == 200
    ids = [row["support_ticket_id"] for row in response.json()]
    assert ids == [case_a["ticket"].support_ticket_id]


def test_customer_supplied_customer_id_filter_on_notifications_is_ignored(client, case_a, case_b):
    response = client.get(
        "/notifications",
        params={"customer_id": case_b["customer"].customer_id},
        headers=case_a["headers"],
    )
    assert response.status_code == 200
    ids = [row["notification_id"] for row in response.json()]
    assert ids == [case_a["notification"].notification_id]


# ---------------------------------------------------------------------------
# support_agent sees across customers, with optional customer_id filter
# ---------------------------------------------------------------------------


def test_support_agent_sees_tickets_across_customers(client, case_a, case_b, support_agent):
    response = client.get("/tickets", headers=support_agent["headers"])
    assert response.status_code == 200
    ids = {row["support_ticket_id"] for row in response.json()}
    assert {case_a["ticket"].support_ticket_id, case_b["ticket"].support_ticket_id} <= ids


def test_support_agent_sees_notifications_across_customers(client, case_a, case_b, support_agent):
    response = client.get("/notifications", headers=support_agent["headers"])
    assert response.status_code == 200
    ids = {row["notification_id"] for row in response.json()}
    assert {
        case_a["notification"].notification_id,
        case_b["notification"].notification_id,
    } <= ids


def test_support_agent_can_filter_tickets_by_customer_id(client, case_a, case_b, support_agent):
    response = client.get(
        "/tickets",
        params={"customer_id": case_b["customer"].customer_id},
        headers=support_agent["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["support_ticket_id"] == case_b["ticket"].support_ticket_id


def test_support_agent_can_filter_notifications_by_customer_id(
    client, case_a, case_b, support_agent
):
    response = client.get(
        "/notifications",
        params={"customer_id": case_b["customer"].customer_id},
        headers=support_agent["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["notification_id"] == case_b["notification"].notification_id
