"""Tests for Task 23: audit logging.

Two layers:
  1. `record_audit_event` unit tests: a real SQLite DB round-trip (insert,
     commit, re-query), mirroring `backend/tests/test_chat_repository.py`'s
     pattern for testing a single-purpose repository/persistence helper.
  2. Endpoint-level tests proving a REAL request through the production
     `app` -- `POST /chat` (Task 15) and `POST /reports/start-of-day`
     (Task 16) -- actually produces an `AuditLog` row, not just that
     `record_audit_event` works in isolation when called directly. Mirrors
     `test_chat_api.py` / `test_reports.py`'s fixture pattern exactly
     (in-memory SQLite `get_db` override, Ollama-touching calls mocked, no
     real network access needed).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, AuditLog, Customer, Device, Driver, Trip, Vehicle
from app.models.enums import BatteryStatus, DeviceStatus, PreferredNotificationMethod
from app.models.knowledge import KnowledgeBaseArticle
from app.security.audit import ACTION_CHAT_ANSWER, ACTION_REPORT_GENERATED, record_audit_event

# ---------------------------------------------------------------------------
# Shared SQLite fixtures (mirrors test_chat_api.py / test_reports.py)
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


# ---------------------------------------------------------------------------
# Layer 1: `record_audit_event` -- direct DB round-trip
# ---------------------------------------------------------------------------


def test_record_audit_event_persists_and_returns_row(db_session):
    entry = record_audit_event(
        db_session,
        actor_id=42,
        actor_role="customer",
        action=ACTION_CHAT_ANSWER,
        description="confidence=0.9 escalated=False",
    )

    assert entry.audit_log_id is not None
    assert entry.created_at is not None

    db_session.expire_all()
    reloaded = db_session.get(AuditLog, entry.audit_log_id)
    assert reloaded is not None
    assert reloaded.actor_id == 42
    assert reloaded.actor_role == "customer"
    assert reloaded.action == ACTION_CHAT_ANSWER
    assert reloaded.description == "confidence=0.9 escalated=False"


def test_record_audit_event_allows_null_description(db_session):
    entry = record_audit_event(
        db_session, actor_id=1, actor_role="support_agent", action="some_future_action"
    )
    assert entry.description is None


def test_record_audit_event_action_is_a_plain_extensible_string(db_session):
    """`AuditLog.action` is deliberately not a DB enum -- an arbitrary new
    action name (e.g. a hypothetical future "ticket_status_change") must be
    writable with no schema change, proving the column doesn't reject
    unknown values."""
    entry = record_audit_event(
        db_session, actor_id=7, actor_role="support_agent", action="ticket_status_change"
    )
    assert entry.action == "ticket_status_change"


# ---------------------------------------------------------------------------
# Layer 2a: POST /chat produces a chat_answer AuditLog row
# ---------------------------------------------------------------------------

_FAKE_EMBEDDING = [0.1] * 768


def _harmless_articles_query(embedding, top_k):  # noqa: ANN001
    return (
        select(KnowledgeBaseArticle)
        .order_by(KnowledgeBaseArticle.knowledge_base_article_id)
        .limit(top_k)
    )


@pytest.fixture(autouse=True)
def _mock_embedding_infra():
    with patch("app.ai.retrieval.embed_text", return_value=_FAKE_EMBEDDING), patch(
        "app.ai.retrieval.build_top_k_articles_query", side_effect=_harmless_articles_query
    ):
        yield


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


@pytest.fixture()
def fleet(db_session):
    customer = Customer(
        full_name="Audit Test Customer",
        email="audit-customer@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)

    driver = Driver(
        customer_id=customer.customer_id, full_name="Audit Driver", license_number="LIC-AUDIT-001"
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number="REG-AUDIT-001",
        make="MakeAudit",
        model="ModelAudit",
        year=2020,
    )
    db_session.add_all([driver, vehicle])
    db_session.commit()
    db_session.refresh(driver)
    db_session.refresh(vehicle)

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trip = Trip(
        driver_id=driver.driver_id,
        vehicle_id=vehicle.vehicle_id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        start_location="Audit-Origin",
        end_location="Audit-Destination",
        distance_km=10.0,
    )
    db_session.add(trip)
    db_session.commit()
    db_session.refresh(trip)

    device = Device(
        customer_id=customer.customer_id,
        serial_number="AUDIT-DEV-001",
        device_type="obd2",
        battery_status=BatteryStatus.OK,
        device_status=DeviceStatus.ACTIVE,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)

    token = create_access_token(subject=customer.customer_id, role="customer")

    return {
        "customer": customer,
        "driver": driver,
        "vehicle": vehicle,
        "trip": trip,
        "device": device,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _seed_article(db_session: Session, *, title: str = "Trip distance FAQ") -> KnowledgeBaseArticle:
    article = KnowledgeBaseArticle(
        title=title,
        content="Trip distance is measured via GPS odometer readings.",
        category="trips",
        embedding=[0.1] * 768,
    )
    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)
    return article


def test_chat_answer_produces_audit_log_entry(client, db_session, fleet):
    # Seeding a KB article + passing trip_id gives `_compute_confidence`
    # real context to work with, so this answer is NOT escalated (mirrors
    # `test_chat_api.py::test_new_session_created_on_first_call`) --
    # the escalated case is covered separately below.
    _seed_article(db_session)

    with patch("app.ai.chat_service.chat_completion", return_value="Some grounded answer."):
        response = client.post(
            "/chat",
            json={
                "query": "how far was my trip?",
                "trip_id": fleet["trip"].trip_id,
                "device_id": fleet["device"].device_id,
            },
            headers=fleet["headers"],
        )
    assert response.status_code == 200
    body = response.json()

    db_session.expire_all()
    entries = db_session.query(AuditLog).filter_by(action=ACTION_CHAT_ANSWER).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_id == fleet["customer"].customer_id
    assert entry.actor_role == "customer"
    assert f"chat_session_id={body['session_id']}" in entry.description
    assert "escalated=False" in entry.description
    assert f"confidence={body['confidence']:.3f}" in entry.description


def test_escalated_chat_answer_audit_log_notes_escalation(client, db_session, fleet):
    """Low-confidence (empty-context) answers get auto-escalated by Task 14
    -- the audit entry must reflect `escalated=True`, not just log
    unconditionally with a stale/default value."""
    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Generic answer with no grounding at all.",
    ):
        response = client.post(
            "/chat",
            json={"query": "totally unrelated question", "device_id": fleet["device"].device_id},
            headers=fleet["headers"],
        )
    assert response.status_code == 200
    body = response.json()
    assert body["escalated"] is True

    db_session.expire_all()
    entry = db_session.query(AuditLog).filter_by(action=ACTION_CHAT_ANSWER).one()
    assert "escalated=True" in entry.description


# ---------------------------------------------------------------------------
# Layer 2b: POST /reports/start-of-day and /end-of-day produce a
# report_generated AuditLog row
# ---------------------------------------------------------------------------


def test_start_of_day_report_produces_audit_log_entry(client, db_session, fleet):
    with patch("app.ai.reports.chat_completion", return_value="Start of day summary."):
        response = client.post("/reports/start-of-day", json={}, headers=fleet["headers"])
    assert response.status_code == 200

    db_session.expire_all()
    entries = db_session.query(AuditLog).filter_by(action=ACTION_REPORT_GENERATED).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_id == fleet["customer"].customer_id
    assert entry.actor_role == "customer"
    assert "report_type=start_of_day" in entry.description
    assert f"customer_id={fleet['customer'].customer_id}" in entry.description


def test_end_of_day_report_produces_audit_log_entry(client, db_session, fleet):
    with patch("app.ai.reports.chat_completion", return_value="End of day summary."):
        response = client.post("/reports/end-of-day", json={}, headers=fleet["headers"])
    assert response.status_code == 200

    db_session.expire_all()
    entry = db_session.query(AuditLog).filter_by(action=ACTION_REPORT_GENERATED).one()
    assert "report_type=end_of_day" in entry.description
