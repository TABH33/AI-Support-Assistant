"""`TelematicsDataSource`: the read-only interface later AI/RAG layers (Tasks
11-16) depend on, instead of importing SQLAlchemy models or a `Session`
directly.

Why this exists (mirrors the plan's "Future Work" framing): this POC is
Postgres-backed today, but the plan calls out a possible future
Databricks-backed replacement. If Tasks 11-16 imported `app.models.*` and
built `db.query(...)` calls themselves, swapping the backing store later
would mean touching every RAG/LLM call site. Routing all of that access
through one interface makes the swap a single new class
(`DatabricksDataSource`, not built in this plan) plus one dependency
rewire in `get_data_source()` below -- nothing downstream changes.

Protocol, not ABC: a `typing.Protocol` is structural -- a conforming class
satisfies the interface by having the right methods/signatures, with no
required inheritance. That matters for the decoupling goal above: a
hypothetical `DatabricksDataSource` could be written against the Databricks
SDK with zero import of this module (or of SQLAlchemy) and still satisfy
`TelematicsDataSource`. An ABC would work too, but forces every
implementation to inherit from a class that (transitively, via type hints)
lives in the same module as this docstring's SQLAlchemy-flavored examples --
a Protocol keeps the *interface* import-clean of any particular backend.
`@runtime_checkable` is added so `isinstance(x, TelematicsDataSource)` works
for a quick structural check (used by this task's own test), though per
`typing` docs that check only verifies method *names* exist, not signatures
-- static type-checking (mypy) is what actually verifies signatures, and
`SyntheticDataSource` also inherits from this Protocol explicitly (allowed,
and it's how `synthetic.py` gets that static check for free).

Return types: methods return the real SQLAlchemy model instances
(`Trip`, `DrivingEvent`, `KnowledgeBaseArticle`, ...) rather than
hand-rolled DTOs/dataclasses. That's a deliberate, pragmatic call: this
plan builds exactly one implementation (`SyntheticDataSource`), so a
parallel DTO layer today would be speculative -- extra code with no second
consumer to prove it's the right shape. The cost is deferred, not avoided:
if/when a second, non-SQLAlchemy implementation is actually built, *that*
implementation would need to either construct duck-typed lookalike objects
or this interface would gain real DTOs at that point, updating this file
and both implementations together. Until then, the interface boundary
itself (not the object's concrete class) is what keeps callers decoupled --
Tasks 11-16 must depend on `TelematicsDataSource`'s method signatures, never
reach past it to `app.models`/`Session` themselves.

Scope note: these methods take explicit IDs and return whatever exists for
that ID -- no customer/RBAC scoping is baked in here. That mirrors the
read-only, ID-keyed shape of the brief's own examples
(`get_driving_events(trip_id)`, `get_trip(trip_id)`,
`get_knowledge_base_articles()`) and keeps the interface a plain data
accessor. Callers that need customer isolation (e.g. Task 12's chat engine,
scoped to the customer on the current `ChatSession`) are responsible for
only ever looking up IDs that already belong to that customer -- the same
division of responsibility `app/api/telematics.py` uses between its
`_scoped_*` query helpers (authorization) and the plain `db.query(...)`
calls beneath them (data access). Baking RBAC into this interface would
also be a poor fit for a future Databricks-backed implementation, which
won't have this app's `CurrentUser`/JWT concepts at all.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.knowledge import KnowledgeBaseArticle
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle


@runtime_checkable
class TelematicsDataSource(Protocol):
    """Read-only access to telematics + knowledge-base data, independent of
    the underlying storage engine."""

    def get_driver(self, driver_id: int) -> Driver | None:
        """Return the `Driver` with `driver_id`, or `None` if it doesn't exist."""
        ...

    def get_vehicle(self, vehicle_id: int) -> Vehicle | None:
        """Return the `Vehicle` with `vehicle_id`, or `None` if it doesn't exist."""
        ...

    def get_trip(self, trip_id: int) -> Trip | None:
        """Return the `Trip` with `trip_id`, or `None` if it doesn't exist."""
        ...

    def get_trips_for_driver(self, driver_id: int) -> list[Trip]:
        """Return all `Trip`s belonging to `driver_id`, ordered oldest-first
        (by `trip_id`). Empty list if the driver has no trips (or doesn't
        exist) -- not an error, matching the other list-returning methods
        here."""
        ...

    def get_driving_events(self, trip_id: int) -> list[DrivingEvent]:
        """Return all `DrivingEvent`s recorded for `trip_id`, ordered
        chronologically (by `event_time`). Empty list if the trip has no
        events (or doesn't exist)."""
        ...

    def get_knowledge_base_articles(
        self, category: str | None = None
    ) -> list[KnowledgeBaseArticle]:
        """Return knowledge-base articles (the RAG corpus), optionally
        filtered to a single `category`. `category=None` (the default)
        returns the full corpus."""
        ...
