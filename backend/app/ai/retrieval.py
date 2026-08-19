"""RAG retrieval service (Task 12): `retrieve_context`.

Combines two independent lookups into one `RetrievedContext`:

1. Top-k `KnowledgeBaseArticle` rows, ranked by pgvector cosine distance
   between the query's embedding (via Task 11's `embed_text`) and each
   article's `embedding` column. This is the one piece of this module that
   is genuinely Postgres-only: pgvector's `<=>` operator (exposed here via
   `KnowledgeBaseArticle.embedding.cosine_distance(...)`) has no SQLite
   equivalent at all -- not even a degraded fallback, unlike the
   `Vector` column type itself (which does round-trip on SQLite as a plain
   string, per `test_models.py`'s `test_knowledge_base_article_embedding_
   round_trips`). `build_top_k_articles_query` builds the real statement
   that will run against Postgres; it is verified here only by compiling it
   against the `postgresql` dialect (see `tests/test_retrieval.py`), never
   by executing it -- there is no live Postgres+pgvector instance available
   to this task. `rank_articles_by_similarity` is a pure-Python
   re-implementation of the same ranking (plain cosine similarity over
   plain lists) that exists specifically so the *ranking logic itself* --
   "does a query about harsh braking surface the harsh-braking article
   first" -- has a deterministic, SQLite/Postgres-independent test.

2. `Driver`/`Vehicle`/`Trip`/`DrivingEvent` rows for whichever ids the
   caller passes, via `TelematicsDataSource` (Task 10) -- never a raw
   `db.query(...)`. Every one of these lookups is a tenant-scoped
   ID-keyed method (`get_driver`, `get_vehicle`, `get_trip`,
   `get_driving_events`), so `retrieve_context` accepts and forwards a
   `customer_id` to every one of them. This is a deliberate expansion of
   the plan's original `retrieve_context(query, driver_id=None,
   trip_id=None, vehicle_id=None, top_k=3)` signature, not scope creep:
   `driver_id`/`trip_id`/`vehicle_id` here are exactly the kind of
   LLM-resolved-from-a-user-question ids `base.py`'s `customer_id`
   docstring calls out as the reason Task 10 added tenant-scoping in the
   first place. Skipping it would let one customer's AI chat resolve and
   surface another customer's telematics data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.ai.embeddings import embed_text
from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.models.knowledge import KnowledgeBaseArticle
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle

DEFAULT_TOP_K = 3


@dataclass
class RetrievedContext:
    """The combined result of one `retrieve_context` call: the top-k
    knowledge-base articles for the query, plus whatever telematics rows
    are relevant to the ids the caller passed in. Fields for ids that
    weren't passed (or that didn't resolve -- including because
    `customer_id` scoping rejected them) are `None`/empty, never omitted --
    callers (the LLM prompt-assembly step, Tasks 13-15) can rely on every
    field always being present."""

    query: str
    articles: list[KnowledgeBaseArticle]
    driver: Driver | None = None
    vehicle: Vehicle | None = None
    trip: Trip | None = None
    driving_events: list[DrivingEvent] = field(default_factory=list)


def build_top_k_articles_query(query_embedding: list[float], top_k: int) -> Select:
    """The real pgvector similarity query: order the full
    `knowledge_base_articles` table by cosine distance (`<=>`) to
    `query_embedding` and take the closest `top_k`. Postgres-only --
    `cosine_distance` compiles to pgvector's `<=>` operator, which SQLite
    has no equivalent for. Verified by dialect-compilation, not execution
    (see `tests/test_retrieval.py`)."""
    return (
        select(KnowledgeBaseArticle)
        .order_by(KnowledgeBaseArticle.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_articles_by_similarity(
    articles: list[KnowledgeBaseArticle],
    query_embedding: list[float],
    top_k: int = DEFAULT_TOP_K,
) -> list[KnowledgeBaseArticle]:
    """Pure-Python stand-in for `build_top_k_articles_query`'s ranking:
    plain cosine similarity over plain Python lists, sorted descending
    (most similar first) and truncated to `top_k`. This performs the same
    ranking pgvector's `ORDER BY embedding <=> query_vector LIMIT top_k`
    does server-side, just without a database -- it exists so the ranking
    *logic* is testable without Postgres. Articles with no embedding yet
    (nullable column, per `KnowledgeBaseArticle`) are skipped, matching
    pgvector's own behavior of NULLS LAST for a NULL vector on that
    operator."""
    scored = [
        (_cosine_similarity(article.embedding, query_embedding), article)
        for article in articles
        if article.embedding
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [article for _, article in scored[:top_k]]


def retrieve_context(
    query: str,
    driver_id: int | None = None,
    trip_id: int | None = None,
    vehicle_id: int | None = None,
    customer_id: int | None = None,
    top_k: int = DEFAULT_TOP_K,
    *,
    db: Session,
    data_source: TelematicsDataSource | None = None,
) -> RetrievedContext:
    """Embed `query` (Task 11's `embed_text`), pull the top-`top_k`
    knowledge-base articles by pgvector cosine similarity, and attach
    whichever `Driver`/`Vehicle`/`Trip`/`DrivingEvent` rows the passed ids
    resolve to via `TelematicsDataSource` -- every one of those lookups is
    scoped to `customer_id` (see module docstring). `db` is required (the
    pgvector query is issued through it directly, same as
    `app.ai.index_kb`'s `db.commit()` -- the one place this module reaches
    past the `TelematicsDataSource` interface, because the interface has no
    vector-similarity method); `data_source` defaults to
    `SyntheticDataSource(db)` when not given, matching `index_knowledge_base`'s
    own `data_source: TelematicsDataSource | None = None` pattern.

    `driver_id`/`vehicle_id`/`trip_id` are independent: passing one doesn't
    imply pulling the others (e.g. passing only `trip_id` does NOT also
    populate `driver`/`vehicle` from that trip). Passing `trip_id` also
    pulls that trip's `DrivingEvent`s.
    """
    ds = data_source if data_source is not None else SyntheticDataSource(db)

    query_embedding = embed_text(query)

    statement = build_top_k_articles_query(query_embedding, top_k)
    articles = list(db.execute(statement).scalars().all())

    driver = ds.get_driver(driver_id, customer_id=customer_id) if driver_id is not None else None
    vehicle = (
        ds.get_vehicle(vehicle_id, customer_id=customer_id) if vehicle_id is not None else None
    )
    trip = ds.get_trip(trip_id, customer_id=customer_id) if trip_id is not None else None
    driving_events = (
        ds.get_driving_events(trip_id, customer_id=customer_id) if trip_id is not None else []
    )

    return RetrievedContext(
        query=query,
        articles=articles,
        driver=driver,
        vehicle=vehicle,
        trip=trip,
        driving_events=driving_events,
    )
