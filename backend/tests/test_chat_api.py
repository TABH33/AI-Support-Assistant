"""Tests for Task 15: `POST /chat`.

Mirrors `test_telematics_api.py`'s pattern: exercises the *real* production
`app` from `app.main` via FastAPI's `TestClient`, with `get_db` overridden
to a temporary in-memory SQLite session (no live Postgres required).

Ollama-touching layers are mocked, following Tasks 11-13's established
pattern:
  * `app.ai.retrieval.embed_text` -- patched (autouse) so `retrieve_context`
    never makes a real embeddings call.
  * `app.ai.retrieval.build_top_k_articles_query` -- patched (autouse) to a
    plain, pgvector-free SELECT. The real query uses pgvector's `<=>`
    cosine-distance operator, which has no SQLite equivalent at all (see
    `app/ai/retrieval.py`'s module docstring and `test_retrieval.py`'s
    cross-tenant test, which does the exact same swap) -- this is an
    infrastructure workaround for the SQLite test fallback, not an
    "Ollama-touching" mock per se, but it's required for `retrieve_context`
    to run against this suite's SQLite `db` at all.
  * `app.ai.chat_service.chat_completion` -- patched per-test (the real
    Ollama chat call), following `test_chat_service.py`'s pattern exactly.

No test in this file makes a real network call; they would still pass with
network access disabled.

Two customers ("A" and "B") are each given a full, separate fleet (driver,
vehicle, trip, device) so the cross-tenant test can prove that a client-
supplied `trip_id`/`driver_id` belonging to a DIFFERENT customer is never
reflected into the AI's context/answer for the caller -- mirroring
`test_telematics_api.py`'s isolation-test rigor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.chat_service import FALLBACK_TEXT
from app.ai.route_planning import (
    GEOCODING_FAILED_TEXT,
    ROUTE_DATA_UNAVAILABLE_TEXT,
    UNAVAILABLE_REASON_GEOCODING,
    UNAVAILABLE_REASON_SERVICE,
    RoutePlanResult,
)
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Customer,
    Device,
    Driver,
    SupportTicket,
    Trip,
    Vehicle,
)
from app.models.enums import BatteryStatus, DeviceStatus, PreferredNotificationMethod
from app.models.knowledge import KnowledgeBaseArticle

_FAKE_EMBEDDING = [0.1] * 768


def _harmless_articles_query(embedding, top_k):  # noqa: ANN001
    """Stand-in for `build_top_k_articles_query`: a plain SELECT with no
    pgvector operator, so it can genuinely run against SQLite. See module
    docstring."""
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


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Chat Test Customer {tag}",
        email=f"chat-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _build_fleet(db_session: Session, *, tag: str) -> dict:
    customer = _make_customer(db_session, tag=tag)

    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Driver {tag}",
        license_number=f"LIC-{tag}-001",
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{tag}-001",
        make=f"Make{tag}",
        model=f"Model{tag}",
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
        start_location=f"Origin-{tag}-Secretville",
        end_location=f"Destination-{tag}-Hiddentown",
        distance_km=42.5,
    )
    db_session.add(trip)
    db_session.commit()
    db_session.refresh(trip)

    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"CHAT-{tag}-DEV-001",
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
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
def fleet_a(db_session):
    return _build_fleet(db_session, tag="A")


@pytest.fixture()
def fleet_b(db_session):
    return _build_fleet(db_session, tag="B")


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


# ---------------------------------------------------------------------------
# Unauthenticated access is rejected
# ---------------------------------------------------------------------------


def test_unauthenticated_request_is_rejected(client):
    response = client.post("/chat", json={"query": "hello"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# New session created on first call
# ---------------------------------------------------------------------------


def test_new_session_created_on_first_call(client, db_session, fleet_a):
    _seed_article(db_session)

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Your trip covered 42.5 km.",
    ) as mock_chat:
        response = client.post(
            "/chat",
            json={
                "query": "how far was my trip?",
                "trip_id": fleet_a["trip"].trip_id,
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["escalated"] is False
    assert body["answer"] == "Your trip covered 42.5 km."
    assert body["confidence"] >= settings.escalation_confidence_threshold
    assert isinstance(body["session_id"], int)
    mock_chat.assert_called_once()

    # A new ChatSession row was actually created, owned by the caller.
    db_session.expire_all()
    sessions = db_session.query(ChatSession).all()
    assert len(sessions) == 1
    assert sessions[0].chat_session_id == body["session_id"]
    assert sessions[0].customer_id == fleet_a["customer"].customer_id
    assert sessions[0].device_id == fleet_a["device"].device_id

    # Both the user question and the assistant answer were persisted.
    messages = (
        db_session.query(ChatMessage)
        .filter_by(chat_session_id=body["session_id"])
        .order_by(ChatMessage.chat_message_id)
        .all()
    )
    assert len(messages) == 2
    assert messages[0].role.value == "user"
    assert messages[0].content == "how far was my trip?"
    assert messages[0].feedback is None
    assert messages[1].role.value == "assistant"
    assert messages[1].content == "Your trip covered 42.5 km."


def test_report_intent_routes_to_report_generator_not_rag(client, db_session, fleet_a):
    """A "give me the daily report" style question must bypass RAG/escalation
    entirely and return the real report text -- reproduces and verifies the
    fix for a live bug where the chat widget's report request always fell
    through to the knowledge-base search, found nothing, and returned the
    escalation fallback text instead of the report."""
    with patch(
        "app.api.chat.generate_end_of_day_report",
        return_value="End-of-day report: 3 trips, 0 harsh braking events.",
    ) as mock_report, patch("app.ai.chat_service.chat_completion") as mock_chat:
        response = client.post(
            "/chat",
            json={
                "query": "give me the daily report",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "End-of-day report: 3 trips, 0 harsh braking events."
    assert body["escalated"] is False
    assert body["confidence"] == 1.0
    mock_report.assert_called_once()
    # The report path must never touch the RAG/LLM chat completion -- if it
    # did, this would silently regress back into the original bug whenever
    # the mocked report generator happens to also return non-fallback text.
    mock_chat.assert_not_called()

    db_session.expire_all()
    messages = (
        db_session.query(ChatMessage)
        .filter_by(chat_session_id=body["session_id"])
        .order_by(ChatMessage.chat_message_id)
        .all()
    )
    assert messages[1].content == "End-of-day report: 3 trips, 0 harsh braking events."


def test_route_plan_intent_routes_to_route_planning_not_rag(client, db_session, fleet_a):
    """A "plan a trip from X to Y" question must bypass RAG entirely and
    return a route summary generated from build_route_plan/summarize_route_plan,
    never touching retrieve_context/chat_service's RAG path."""
    route_result = RoutePlanResult(
        distance_km=23.4,
        duration_min=38.2,
        geometry={"type": "LineString", "coordinates": []},
        warnings=[],
    )
    with (
        patch("app.api.chat.build_route_plan", return_value=route_result) as mock_build,
        patch(
            "app.api.chat.summarize_route_plan", return_value="It's a 23.4km, 38 minute trip."
        ) as mock_summarize,
        patch("app.ai.chat_service.chat_completion") as mock_chat,
    ):
        response = client.post(
            "/chat",
            json={
                "query": "plan a trip from Sydney CBD to Parramatta",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "It's a 23.4km, 38 minute trip."
    assert body["confidence"] == 1.0
    assert body["escalated"] is False
    assert body["route_plan"]["distance_km"] == 23.4
    mock_build.assert_called_once_with("Sydney CBD", "Parramatta", db=ANY)
    mock_summarize.assert_called_once()
    mock_chat.assert_not_called()


def test_route_plan_intent_with_only_destination_asks_for_origin(client, fleet_a):
    with patch("app.api.chat.build_route_plan") as mock_build:
        response = client.post(
            "/chat",
            json={
                "query": "warnings on the route to Parramatta",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert "starting point" in body["answer"].lower()
    assert body["route_plan"] is None
    mock_build.assert_not_called()


def test_route_plan_intent_unavailable_returns_graceful_fallback(client, fleet_a):
    unavailable_result = RoutePlanResult(
        distance_km=None,
        duration_min=None,
        geometry=None,
        unavailable=True,
        unavailable_reason=UNAVAILABLE_REASON_SERVICE,
        unavailable_message=ROUTE_DATA_UNAVAILABLE_TEXT,
    )
    with patch("app.api.chat.build_route_plan", return_value=unavailable_result):
        response = client.post(
            "/chat",
            json={
                "query": "plan a trip from Nowhere to Parramatta",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ROUTE_DATA_UNAVAILABLE_TEXT
    assert body["escalated"] is False
    assert body["route_plan"] is None


# ---------------------------------------------------------------------------
# Final-review Fix 4: route-plan intent must not swallow trailing sentence
# text, and must not hijack ordinary telematics/RAG questions.
# ---------------------------------------------------------------------------


def test_route_plan_intent_does_not_swallow_trailing_words_into_destination(
    client, fleet_a
):
    """"...to Parramatta tomorrow morning" must geocode "Parramatta", not
    the literal string "Parramatta tomorrow morning" (which ORS cannot
    resolve, producing a spurious "route data unavailable")."""
    route_result = RoutePlanResult(
        distance_km=23.4,
        duration_min=38.2,
        geometry={"type": "LineString", "coordinates": []},
        warnings=[],
    )
    with (
        patch("app.api.chat.build_route_plan", return_value=route_result) as mock_build,
        patch("app.api.chat.summarize_route_plan", return_value="A 23.4km trip."),
    ):
        response = client.post(
            "/chat",
            json={
                "query": "plan a trip from Sydney CBD to Parramatta tomorrow morning",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    mock_build.assert_called_once_with("Sydney CBD", "Parramatta", db=ANY)


@pytest.mark.parametrize(
    "query",
    [
        # Contains "drive", "from" and "to" -- an ordinary question about
        # driving, not a request to plan a route.
        "How long does it take to drive from home to work?",
        "why did my driver drive from the depot to the client site so slowly",
    ],
)
def test_ordinary_driving_question_falls_through_to_rag_not_route_planning(
    client, fleet_a, query
):
    with (
        patch("app.api.chat.build_route_plan") as mock_build,
        patch(
            "app.ai.chat_service.chat_completion",
            return_value='{"answer": "About 25 minutes.", "confidence": 0.9}',
        ) as mock_chat,
    ):
        response = client.post(
            "/chat",
            json={"query": query, "device_id": fleet_a["device"].device_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    mock_build.assert_not_called()
    mock_chat.assert_called_once()


# ---------------------------------------------------------------------------
# Final-review Fix 5: a bad place name gets spelling advice, not "try again".
# ---------------------------------------------------------------------------


def test_route_plan_geocoding_failure_returns_the_place_specific_message(client, fleet_a):
    geocoding_failure = RoutePlanResult(
        distance_km=None,
        duration_min=None,
        geometry=None,
        unavailable=True,
        unavailable_reason=UNAVAILABLE_REASON_GEOCODING,
        unavailable_message=GEOCODING_FAILED_TEXT.format(place="Parramattaa"),
    )
    with patch("app.api.chat.build_route_plan", return_value=geocoding_failure):
        response = client.post(
            "/chat",
            json={
                "query": "plan a trip from Sydney CBD to Parramattaa",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert "Parramattaa" in body["answer"]
    assert body["answer"] != ROUTE_DATA_UNAVAILABLE_TEXT
    assert body["route_plan"] is None


def test_new_session_requires_device_id(client, fleet_a):
    with patch("app.ai.chat_service.chat_completion", return_value="some answer"):
        response = client.post(
            "/chat",
            json={"query": "hello, no device id given"},
            headers=fleet_a["headers"],
        )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Existing session reused on subsequent calls
# ---------------------------------------------------------------------------


def test_existing_session_reused_on_subsequent_calls(client, db_session, fleet_a):
    _seed_article(db_session)

    with patch("app.ai.chat_service.chat_completion", return_value="First answer."):
        first_response = client.post(
            "/chat",
            json={
                "query": "first question",
                "trip_id": fleet_a["trip"].trip_id,
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )
    assert first_response.status_code == 200
    session_id = first_response.json()["session_id"]

    with patch("app.ai.chat_service.chat_completion", return_value="Second answer."):
        second_response = client.post(
            "/chat",
            json={
                "query": "second question",
                "session_id": session_id,
                "trip_id": fleet_a["trip"].trip_id,
            },
            headers=fleet_a["headers"],
        )
    assert second_response.status_code == 200
    assert second_response.json()["session_id"] == session_id
    assert second_response.json()["answer"] == "Second answer."

    # Exactly one ChatSession row exists -- the second call reused it rather
    # than creating a new one.
    db_session.expire_all()
    assert db_session.query(ChatSession).count() == 1

    # Four ChatMessage rows total: 2 user+assistant pairs, one per call.
    messages = (
        db_session.query(ChatMessage)
        .filter_by(chat_session_id=session_id)
        .order_by(ChatMessage.chat_message_id)
        .all()
    )
    assert [m.content for m in messages] == [
        "first question",
        "First answer.",
        "second question",
        "Second answer.",
    ]


def test_customer_cannot_reuse_another_customers_session(client, db_session, fleet_a, fleet_b):
    with patch("app.ai.chat_service.chat_completion", return_value="An answer."):
        create_response = client.post(
            "/chat",
            json={"query": "b's question", "device_id": fleet_b["device"].device_id},
            headers=fleet_b["headers"],
        )
    b_session_id = create_response.json()["session_id"]

    with patch("app.ai.chat_service.chat_completion", return_value="Should not be reached."):
        response = client.post(
            "/chat",
            json={"query": "trying to piggyback", "session_id": b_session_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Escalation path
# ---------------------------------------------------------------------------


def test_escalation_path_returns_escalated_true_and_fallback_text(client, db_session, fleet_a):
    # No trip/driver/vehicle context resolved and no KB articles seeded, so
    # `_compute_confidence` pins confidence to the 0.1 floor -- below
    # `settings.escalation_confidence_threshold` (0.6 default) -- regardless
    # of what the (mocked) LLM says.
    assert 0.1 < settings.escalation_confidence_threshold

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="I think it might possibly be a battery issue?",
    ):
        response = client.post(
            "/chat",
            json={
                "query": "why is my device offline?",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["escalated"] is True
    assert body["answer"] == FALLBACK_TEXT
    assert body["confidence"] <= 0.1

    db_session.expire_all()
    tickets = (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=body["session_id"])
        .all()
    )
    assert len(tickets) == 1
    assert tickets[0].customer_id == fleet_a["customer"].customer_id

    # The persisted assistant message is the fallback text, not the raw LLM guess.
    messages = (
        db_session.query(ChatMessage)
        .filter_by(chat_session_id=body["session_id"], role="assistant")
        .all()
    )
    assert messages[0].content == FALLBACK_TEXT


def test_two_consecutive_low_confidence_turns_reuse_the_same_ticket(client, db_session, fleet_a):
    """Final-review Fix 2 regression test: before the fix, a second
    low-confidence answer in the SAME session hit Task 8's DB-level
    ChatSession 1 -> 0..1 SupportTicket UNIQUE constraint and raised an
    unhandled `IntegrityError` all the way up to an unhandled 500 (Task 14's
    `handle_answer` called `create_support_ticket` unconditionally, with no
    select-first check). The second turn must instead return a normal 200
    response and reuse the SAME `support_ticket_id` as the first, with
    exactly one `SupportTicket` row in the DB afterward."""
    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="I think it might possibly be a battery issue?",
    ):
        first_response = client.post(
            "/chat",
            json={
                "query": "why is my device offline?",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["escalated"] is True
    session_id = first_body["session_id"]

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Still not sure, maybe try rebooting?",
    ):
        second_response = client.post(
            "/chat",
            json={
                "query": "it's still offline, what now?",
                "session_id": session_id,
            },
            headers=fleet_a["headers"],
        )

    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["escalated"] is True
    assert second_body["answer"] == FALLBACK_TEXT

    db_session.expire_all()
    tickets = (
        db_session.query(SupportTicket).filter_by(chat_session_id=session_id).all()
    )
    assert len(tickets) == 1
    assert tickets[0].support_ticket_id is not None


def test_failure_after_ticket_and_notification_leaves_no_stranded_rows(client, db_session, fleet_a):
    """Final-review Fix 6 regression test: `POST /chat` used to perform
    several independent `db.commit()` calls in sequence (session creation,
    ticket/notification creation, message persistence, audit logging). If
    anything failed between the ticket/notification commit and the
    message-persistence commit, a support agent would end up with a
    SupportTicket pointing at a ChatSession with zero messages -- nothing to
    act on.

    The route now stages everything (session, ticket, notification, both
    ChatMessage rows, the AuditLog row) in ONE transaction and commits
    exactly once at the very end. This simulates a failure AFTER the
    escalation ticket/notification would have been flushed but BEFORE the
    single final commit (patching `record_audit_event`, the very last write
    before that commit, to raise) and proves the whole transaction rolls
    back together -- no orphaned SupportTicket, no orphaned ChatSession, no
    partial ChatMessage rows survive."""
    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="I think it might possibly be a battery issue?",
    ), patch(
        "app.api.chat.record_audit_event", side_effect=RuntimeError("simulated audit failure")
    ):
        with pytest.raises(RuntimeError):
            client.post(
                "/chat",
                json={
                    "query": "why is my device offline?",
                    "device_id": fleet_a["device"].device_id,
                },
                headers=fleet_a["headers"],
            )

    # Simulates what the real `get_db` dependency's `finally: db.close()`
    # would do on request teardown (this test's `client` fixture reuses one
    # session directly across the whole test without closing it -- see
    # module docstring -- so the rollback is done explicitly here to prove
    # the property, not because production code needs a manual rollback).
    db_session.rollback()
    db_session.expire_all()

    assert db_session.query(SupportTicket).count() == 0
    assert db_session.query(ChatSession).count() == 0
    assert db_session.query(ChatMessage).count() == 0


# ---------------------------------------------------------------------------
# Cross-tenant isolation: another customer's ids must not leak into context
# ---------------------------------------------------------------------------


def test_customer_supplied_cross_tenant_trip_and_driver_ids_do_not_leak(
    client, db_session, fleet_a, fleet_b
):
    """Customer A supplies customer B's trip_id/driver_id. `retrieve_context`
    must resolve neither (since `customer_id` passed through is A's), so
    none of B's trip/driver data ever reaches the LLM prompt -- and, as a
    consequence, confidence is computed as if no telematics context was
    found at all (the escalation path engages)."""
    with patch(
        "app.ai.chat_service.chat_completion", return_value="Generic answer with no real context."
    ) as mock_chat:
        response = client.post(
            "/chat",
            json={
                "query": "tell me about this trip",
                "trip_id": fleet_b["trip"].trip_id,
                "driver_id": fleet_b["driver"].driver_id,
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()

    # Cross-tenant data never reached the prompt sent to the LLM.
    mock_chat.assert_called_once()
    messages_sent = mock_chat.call_args[0][0]
    prompt_text = " ".join(m["content"] for m in messages_sent)
    assert fleet_b["driver"].full_name not in prompt_text
    assert fleet_b["trip"].start_location not in prompt_text
    assert fleet_b["trip"].end_location not in prompt_text
    assert "no driver/vehicle/trip data was resolved" in prompt_text

    # Confidence reflects "nothing was found" -- the strongest available
    # signal that no cross-tenant data was silently included.
    assert body["confidence"] <= 0.1
    assert body["escalated"] is True

    # And, for good measure: the ChatSession created belongs to customer A,
    # not customer B -- retrieval was scoped to the caller's own customer_id.
    db_session.expire_all()
    chat_session = db_session.get(ChatSession, body["session_id"])
    assert chat_session.customer_id == fleet_a["customer"].customer_id


def test_customer_supplied_cross_tenant_vehicle_id_does_not_leak(client, db_session, fleet_a, fleet_b):
    with patch(
        "app.ai.chat_service.chat_completion", return_value="Generic answer."
    ) as mock_chat:
        response = client.post(
            "/chat",
            json={
                "query": "tell me about this vehicle",
                "vehicle_id": fleet_b["vehicle"].vehicle_id,
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    mock_chat.assert_called_once()
    messages_sent = mock_chat.call_args[0][0]
    prompt_text = " ".join(m["content"] for m in messages_sent)
    assert fleet_b["vehicle"].registration_number not in prompt_text
    assert fleet_b["vehicle"].model not in prompt_text


# ---------------------------------------------------------------------------
# support_agent scoping
# ---------------------------------------------------------------------------


@pytest.fixture()
def support_agent_headers(db_session):
    from app.models.support_agent import SupportAgent
    from app.models.enums import AccessLevel

    agent = SupportAgent(
        full_name="Chat Test Support Agent",
        email="chat-support-agent@example.test",
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


def test_support_agent_must_supply_customer_id_to_start_a_new_session(
    client, fleet_a, support_agent_headers
):
    with patch("app.ai.chat_service.chat_completion", return_value="answer"):
        response = client.post(
            "/chat",
            json={"query": "agent question", "device_id": fleet_a["device"].device_id},
            headers=support_agent_headers,
        )
    assert response.status_code == 400


def test_support_agent_can_start_session_for_a_specific_customer(
    client, db_session, fleet_a, support_agent_headers
):
    with patch("app.ai.chat_service.chat_completion", return_value="answer for customer A"):
        response = client.post(
            "/chat",
            json={
                "query": "agent question on behalf of customer A",
                "customer_id": fleet_a["customer"].customer_id,
                "device_id": fleet_a["device"].device_id,
            },
            headers=support_agent_headers,
        )
    assert response.status_code == 200
    db_session.expire_all()
    chat_session = db_session.get(ChatSession, response.json()["session_id"])
    assert chat_session.customer_id == fleet_a["customer"].customer_id


def test_support_agent_can_reuse_any_customers_session(client, db_session, fleet_a, support_agent_headers):
    # Both turns resolve real telematics context (+ a seeded KB article) so
    # confidence stays above the escalation threshold on both calls -- a
    # second low-confidence escalation for the same session is exercised
    # separately (see test_two_consecutive_low_confidence_turns_reuse_the_same_ticket
    # below and test_escalation.py), which isn't what this test is about.
    _seed_article(db_session)

    with patch("app.ai.chat_service.chat_completion", return_value="An answer."):
        create_response = client.post(
            "/chat",
            json={
                "query": "customer's own question",
                "device_id": fleet_a["device"].device_id,
                "trip_id": fleet_a["trip"].trip_id,
            },
            headers=fleet_a["headers"],
        )
    session_id = create_response.json()["session_id"]
    assert create_response.json()["escalated"] is False

    with patch("app.ai.chat_service.chat_completion", return_value="Agent follow-up answer."):
        response = client.post(
            "/chat",
            json={
                "query": "agent follow-up",
                "session_id": session_id,
                "trip_id": fleet_a["trip"].trip_id,
            },
            headers=support_agent_headers,
        )
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert response.json()["escalated"] is False
