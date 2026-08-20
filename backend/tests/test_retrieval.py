"""Tests for `app.ai.retrieval` (Task 12).

Three layers, matching the three pieces `retrieve_context` combines:

1. `rank_articles_by_similarity` -- a pure-Python cosine-similarity ranking
   function, tested directly with plain lists/fake `KnowledgeBaseArticle`
   instances (never touching a database). This is what proves "a query
   about harsh braking surfaces the harsh-braking article" deterministically,
   standing in for the real pgvector `<=>` ranking that can't run here.

2. `build_top_k_articles_query` -- the real pgvector query. It genuinely
   cannot execute against SQLite (no `<=>` operator at all), so it's
   verified by compiling it against the `postgresql` dialect and asserting
   the compiled SQL has the right shape (cosine-distance operator, ORDER BY,
   LIMIT) -- proving the query is well-formed without running it. Actual
   ranking behavior against real embedding data in real Postgres is
   unverified here, consistent with how Tasks 4/9/11 handled the same kind
   of live-Postgres gap.

3. `retrieve_context` itself -- the embedding call is mocked
   (`patch("app.ai.retrieval.embed_text")`, following `test_embeddings.py`'s
   pattern), and the pgvector query execution is isolated by mocking either
   `db.execute` directly or the whole `db`, since running it for real is the
   one thing this suite genuinely cannot do. The `TelematicsDataSource`
   lookups (driver/vehicle/trip/driving_events) and their `customer_id`
   scoping ARE exercised for real, against an in-memory SQLite DB through
   `SyntheticDataSource` -- same fixture pattern as `test_datasource.py`
   (Task 10) -- including a genuine two-customer cross-tenant isolation
   proof for `customer_id` passthrough, not just a mock-call assertion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.retrieval import (
    RetrievedContext,
    build_top_k_articles_query,
    rank_articles_by_similarity,
    retrieve_context,
)
from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.models import Base, Customer, Driver, DrivingEvent, KnowledgeBaseArticle, Trip, Vehicle
from app.models.enums import DrivingEventType, PreferredNotificationMethod

FAKE_EMBEDDING = [0.1] * 768


# ---------------------------------------------------------------------------
# rank_articles_by_similarity: pure-Python ranking, no DB involved
# ---------------------------------------------------------------------------


def _article(title: str, embedding: list[float] | None, category: str | None = None):
    return KnowledgeBaseArticle(title=title, content=f"Content for {title}", category=category, embedding=embedding)


def test_rank_articles_by_similarity_surfaces_the_closest_article_first():
    query_embedding = [1.0, 0.0, 0.0]
    harsh_braking = _article("Harsh braking alerts", [0.95, 0.05, 0.0])
    billing = _article("Billing FAQ", [0.0, 0.05, 0.95])
    gps = _article("GPS troubleshooting", [0.5, 0.5, 0.0])

    ranked = rank_articles_by_similarity([billing, gps, harsh_braking], query_embedding, top_k=3)

    assert [a.title for a in ranked] == ["Harsh braking alerts", "GPS troubleshooting", "Billing FAQ"]


def test_rank_articles_by_similarity_respects_top_k():
    query_embedding = [1.0, 0.0, 0.0]
    harsh_braking = _article("Harsh braking alerts", [0.95, 0.05, 0.0])
    billing = _article("Billing FAQ", [0.0, 0.05, 0.95])

    ranked = rank_articles_by_similarity([billing, harsh_braking], query_embedding, top_k=1)

    assert [a.title for a in ranked] == ["Harsh braking alerts"]


def test_rank_articles_by_similarity_skips_articles_without_an_embedding():
    query_embedding = [1.0, 0.0, 0.0]
    unembedded = _article("Not yet indexed", None)
    harsh_braking = _article("Harsh braking alerts", [0.95, 0.05, 0.0])

    ranked = rank_articles_by_similarity([unembedded, harsh_braking], query_embedding, top_k=5)

    assert ranked == [harsh_braking]


def test_rank_articles_by_similarity_returns_empty_list_for_no_articles():
    assert rank_articles_by_similarity([], [1.0, 0.0, 0.0], top_k=3) == []


def test_rank_articles_by_similarity_uses_true_cosine_similarity_not_raw_dot_product():
    """Regression guard for the `/ (norm_a * norm_b)` normalization in
    `_cosine_similarity`. The two fixture embeddings below are chosen so
    that an unnormalized dot product and true cosine similarity rank them
    in OPPOSITE order -- so this test only passes if normalization is
    actually applied, unlike the other fixtures above (whose near-equal
    magnitudes mean dot product and cosine similarity happen to agree)."""
    query_embedding = [1.0, 0.0]
    # High magnitude, poorly aligned with the query: dot product = 1.0,
    # but cosine similarity ~= 0.0995 (mostly orthogonal direction).
    poorly_aligned_but_large = _article("Poorly aligned, large magnitude", [1.0, 10.0])
    # Low magnitude, perfectly aligned with the query: dot product = 0.1,
    # but cosine similarity = 1.0 (identical direction to the query).
    aligned_but_small = _article("Perfectly aligned, small magnitude", [0.1, 0.0])

    ranked = rank_articles_by_similarity(
        [poorly_aligned_but_large, aligned_but_small], query_embedding, top_k=2
    )

    # Raw dot product would rank `poorly_aligned_but_large` first (1.0 > 0.1).
    # True cosine similarity ranks `aligned_but_small` first (1.0 > ~0.0995).
    assert [a.title for a in ranked] == [
        "Perfectly aligned, small magnitude",
        "Poorly aligned, large magnitude",
    ]


# ---------------------------------------------------------------------------
# build_top_k_articles_query: real pgvector query, verified by dialect
# compilation only (no live Postgres available -- see module/test docstrings)
# ---------------------------------------------------------------------------


def test_build_top_k_articles_query_compiles_to_pgvector_cosine_distance_sql():
    query_embedding = [0.25] * 768

    statement = build_top_k_articles_query(query_embedding, top_k=3)
    compiled_sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "knowledge_base_articles" in compiled_sql
    assert "<=>" in compiled_sql  # pgvector's cosine-distance operator
    assert "ORDER BY" in compiled_sql
    assert "LIMIT 3" in compiled_sql
    # Ascending distance = closest-first = most-similar-first, which is the
    # correct ranking. Positively confirm ascending order (no `.desc()`
    # anywhere in the statement) -- a future edit that accidentally added
    # `.desc()` would silently invert the ranking to return the LEAST
    # similar articles, and the assertions above alone wouldn't catch that.
    assert "DESC" not in compiled_sql
    order_by_clause = compiled_sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
    assert order_by_clause.strip().startswith("knowledge_base_articles.embedding <=>")


def test_build_top_k_articles_query_respects_a_different_top_k():
    statement = build_top_k_articles_query([0.1] * 768, top_k=7)
    compiled_sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "LIMIT 7" in compiled_sql


# ---------------------------------------------------------------------------
# retrieve_context: fixtures (mirrors test_datasource.py's pattern)
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
def session(engine):
    with Session(engine) as db_session:
        yield db_session


def _make_customer(session, suffix: str) -> Customer:
    customer = Customer(
        full_name=f"Retrieval Customer {suffix}",
        email=f"rc{suffix}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    return customer


def _make_fleet(session, suffix: str) -> dict:
    customer = _make_customer(session, suffix)
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Driver {suffix}",
        license_number=f"LIC-{suffix}",
    )
    vehicle = Vehicle(
        customer_id=customer.customer_id,
        registration_number=f"REG-{suffix}",
        make="TestMake",
        model="TestModel",
        year=2020,
    )
    session.add_all([driver, vehicle])
    session.flush()

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trip = Trip(
        driver_id=driver.driver_id,
        vehicle_id=vehicle.vehicle_id,
        start_time=start,
        end_time=start + timedelta(hours=1),
        distance_km=12.0,
    )
    session.add(trip)
    session.flush()

    events = [
        DrivingEvent(
            trip_id=trip.trip_id,
            event_type=DrivingEventType.HARSH_BRAKING,
            event_time=start + timedelta(minutes=5),
            details="harsh braking event",
        ),
        DrivingEvent(
            trip_id=trip.trip_id,
            event_type=DrivingEventType.SPEEDING,
            event_time=start + timedelta(minutes=10),
            details="speeding event",
        ),
    ]
    session.add_all(events)
    session.commit()

    return {"customer": customer, "driver": driver, "vehicle": vehicle, "trip": trip, "events": events}


def _mock_db_returning(articles: list[KnowledgeBaseArticle]) -> MagicMock:
    """A `db` stand-in whose `.execute(...).scalars().all()` returns
    `articles`, standing in for the (untestable-here) real pgvector query
    result. Used for tests that aren't specifically about the DataSource
    lookups, so they don't need a real Session at all."""
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = articles
    return db


# ---------------------------------------------------------------------------
# retrieve_context: embedding + article retrieval orchestration
# ---------------------------------------------------------------------------


def test_retrieve_context_embeds_the_query_and_returns_articles_from_db_execute():
    harsh_braking = _article("Harsh braking alerts", [0.9] * 768)
    db = _mock_db_returning([harsh_braking])

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING) as mock_embed:
        result = retrieve_context("harsh braking alert", db=db, data_source=Mock(spec=TelematicsDataSource))

    mock_embed.assert_called_once_with("harsh braking alert")
    assert isinstance(result, RetrievedContext)
    assert result.query == "harsh braking alert"
    assert result.articles == [harsh_braking]
    db.execute.assert_called_once()


def test_retrieve_context_populates_article_similarities_from_real_embeddings():
    """Final-review Fix 4 regression test: `retrieve_context` must thread a
    real cosine-similarity score per retrieved article through to
    `RetrievedContext.article_similarities` (same order/length as
    `articles`), computed from the articles' own `.embedding` values against
    the query embedding -- not just return the bare article count, which is
    all `_compute_confidence` previously had to work with."""
    close_embedding = [1.0, 0.0, 0.0] + [0.0] * 765
    far_embedding = [0.0, 1.0, 0.0] + [0.0] * 765
    close_article = _article("Close match", close_embedding)
    far_article = _article("Far match", far_embedding)
    db = _mock_db_returning([close_article, far_article])

    query_embedding = [1.0, 0.0, 0.0] + [0.0] * 765
    with patch("app.ai.retrieval.embed_text", return_value=query_embedding):
        result = retrieve_context("a question", db=db, data_source=Mock(spec=TelematicsDataSource))

    assert len(result.article_similarities) == 2
    # Identical vector -> cosine similarity 1.0; orthogonal vector -> ~0.0.
    assert result.article_similarities[0] == pytest.approx(1.0)
    assert result.article_similarities[1] == pytest.approx(0.0, abs=1e-9)


def test_retrieve_context_passes_top_k_through_to_the_query():
    db = _mock_db_returning([])

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING), patch(
        "app.ai.retrieval.build_top_k_articles_query"
    ) as mock_build_query:
        retrieve_context("some question", top_k=5, db=db, data_source=Mock(spec=TelematicsDataSource))

    mock_build_query.assert_called_once_with(FAKE_EMBEDDING, 5)


def test_retrieve_context_with_no_ids_leaves_telematics_fields_empty_and_untouched():
    db = _mock_db_returning([])
    ds = Mock(spec=TelematicsDataSource)

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING):
        result = retrieve_context("general question", db=db, data_source=ds)

    assert result.driver is None
    assert result.vehicle is None
    assert result.trip is None
    assert result.driving_events == []
    ds.get_driver.assert_not_called()
    ds.get_vehicle.assert_not_called()
    ds.get_trip.assert_not_called()
    ds.get_driving_events.assert_not_called()


# ---------------------------------------------------------------------------
# retrieve_context: customer_id passthrough to every scoped DataSource call
# (mock-based proof of the exact call signature)
# ---------------------------------------------------------------------------


def test_retrieve_context_passes_customer_id_through_to_every_datasource_call():
    db = _mock_db_returning([])
    ds = Mock(spec=TelematicsDataSource)

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING):
        retrieve_context(
            "what happened on trip 5?",
            driver_id=10,
            trip_id=5,
            vehicle_id=20,
            customer_id=42,
            db=db,
            data_source=ds,
        )

    ds.get_driver.assert_called_once_with(10, customer_id=42)
    ds.get_vehicle.assert_called_once_with(20, customer_id=42)
    ds.get_trip.assert_called_once_with(5, customer_id=42)
    ds.get_driving_events.assert_called_once_with(5, customer_id=42)


def test_retrieve_context_passes_customer_id_none_when_not_given():
    db = _mock_db_returning([])
    ds = Mock(spec=TelematicsDataSource)

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING):
        retrieve_context("what happened on trip 5?", trip_id=5, db=db, data_source=ds)

    ds.get_trip.assert_called_once_with(5, customer_id=None)
    ds.get_driving_events.assert_called_once_with(5, customer_id=None)


# ---------------------------------------------------------------------------
# retrieve_context: trip context attachment
# ---------------------------------------------------------------------------


def test_retrieve_context_attaches_trip_and_driving_events_when_trip_id_passed(session):
    fleet = _make_fleet(session, "TRIP1")
    session.expire_all()
    ds = SyntheticDataSource(session)
    db = _mock_db_returning([])

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING):
        result = retrieve_context(
            "why did the driver brake harshly?",
            trip_id=fleet["trip"].trip_id,
            db=db,
            data_source=ds,
        )

    assert result.trip is not None
    assert result.trip.trip_id == fleet["trip"].trip_id
    assert [e.details for e in result.driving_events] == ["harsh braking event", "speeding event"]
    assert result.driver is None  # driver_id wasn't passed, so it's not pulled
    assert result.vehicle is None


# ---------------------------------------------------------------------------
# retrieve_context: genuine two-customer isolation proof, real SQLite DB +
# real SyntheticDataSource default (data_source not passed) -- this is the
# strongest available proof that customer_id scoping actually blocks
# cross-tenant access, not just that it's forwarded as a kwarg.
# ---------------------------------------------------------------------------


def test_retrieve_context_default_data_source_enforces_cross_tenant_isolation(session):
    fleet_a = _make_fleet(session, "ISO-A")
    fleet_b = _make_fleet(session, "ISO-B")
    trip_b_id = fleet_b["trip"].trip_id
    customer_a_id = fleet_a["customer"].customer_id
    customer_b_id = fleet_b["customer"].customer_id

    # `build_top_k_articles_query` is swapped for a plain, pgvector-free
    # SELECT (no `<=>` operator) so `db.execute(...)` genuinely runs against
    # the real SQLite session below -- exercising the real
    # `SyntheticDataSource(db)` default (and its own internal `db.query(...)`
    # calls) instead of a fully-mocked `db`. Only the pgvector-specific
    # operator is unrunnable on SQLite; a plain SELECT against the same
    # (empty) table isn't.
    harmless_query = lambda embedding, top_k: select(KnowledgeBaseArticle).limit(top_k)  # noqa: E731

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING), patch(
        "app.ai.retrieval.build_top_k_articles_query", side_effect=harmless_query
    ):
        # Customer A asking about customer B's trip must NOT see it.
        blocked = retrieve_context(
            "what happened on this trip?",
            trip_id=trip_b_id,
            customer_id=customer_a_id,
            db=session,
        )

        # Customer B asking about their own trip succeeds.
        allowed = retrieve_context(
            "what happened on this trip?",
            trip_id=trip_b_id,
            customer_id=customer_b_id,
            db=session,
        )

    assert blocked.trip is None
    assert blocked.driving_events == []
    assert allowed.trip is not None
    assert allowed.trip.trip_id == trip_b_id
    assert len(allowed.driving_events) == 2


def test_retrieve_context_defaults_to_synthetic_data_source_when_omitted(session):
    fleet = _make_fleet(session, "DEFAULT")
    driver_id = fleet["driver"].driver_id
    harmless_query = lambda embedding, top_k: select(KnowledgeBaseArticle).limit(top_k)  # noqa: E731

    with patch("app.ai.retrieval.embed_text", return_value=FAKE_EMBEDDING), patch(
        "app.ai.retrieval.build_top_k_articles_query", side_effect=harmless_query
    ):
        result = retrieve_context(
            "trip question",
            driver_id=driver_id,
            db=session,
        )

    assert result.driver is not None
    assert result.driver.driver_id == driver_id
