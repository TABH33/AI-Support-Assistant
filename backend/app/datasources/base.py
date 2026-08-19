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

Scope note (revised after Task 10 review): the ID-keyed methods
(`get_driver`, `get_vehicle`, `get_trip`, `get_trips_for_driver`,
`get_driving_events`) accept an optional `customer_id: int | None = None`.
This is *not* RBAC/JWT re-entering the interface -- it's a plain `int`,
the same primitive `app/repositories/chat.py` (Task 8) already threads
through its own functions (e.g. `create_support_ticket(db,
chat_session_id, customer_id, ...)`). The interface still doesn't know
about `CurrentUser`, roles, or tokens; it only knows "optionally, filter to
rows reachable from this customer_id."

Why this was added: Task 7's RBAC enforcement is one small, fixed set of
REST handlers -- an easy audit choke point where a reviewer can read five
`_scoped_*` helpers and be done. Tasks 12-15 (LLM tool-calling resolving a
`trip_id`/`driver_id` out of a natural-language question) will have many
more call sites, and "the caller must remember to pre-validate the ID"
doesn't hold up as a guarantee across all of them -- the failure mode is a
cross-tenant leak dressed up as the AI confidently answering with someone
else's fleet data. So `customer_id` is the recommended defense-in-depth
mechanism: Tasks 12-15 should pass the calling customer's id on every
lookup derived from user input, and `SyntheticDataSource` enforces it with
the same transitive-join logic `app/api/telematics.py`'s `_scoped_*`
helpers already use (Driver/Vehicle direct `customer_id`; Trip and
DrivingEvent scoped transitively through Driver). Passing `None` (the
default) keeps today's unscoped behavior -- needed for contexts with no
single owning customer (e.g. a support_agent's cross-fleet view, matching
Task 7's own `support_agent`-is-unrestricted rule) -- and keeps a
hypothetical `DatabricksDataSource` free to ignore the parameter if its own
security model handles tenant isolation elsewhere (e.g. row-level security
enforced by the warehouse itself). `get_knowledge_base_articles` has no
`customer_id` parameter -- the knowledge base is global, shared across all
customers, not tenant-scoped data.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.knowledge import KnowledgeBaseArticle
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle


@runtime_checkable
class TelematicsDataSource(Protocol):
    """Read-only access to telematics + knowledge-base data, independent of
    the underlying storage engine."""

    def get_driver(self, driver_id: int, customer_id: int | None = None) -> Driver | None:
        """Return the `Driver` with `driver_id`, or `None` if it doesn't
        exist. If `customer_id` is given, also returns `None` when the
        driver exists but doesn't belong to that customer (tenant-scoping,
        recommended whenever `driver_id` came from user-supplied/LLM-resolved
        input)."""
        ...

    def get_vehicle(self, vehicle_id: int, customer_id: int | None = None) -> Vehicle | None:
        """Return the `Vehicle` with `vehicle_id`, or `None` if it doesn't
        exist. If `customer_id` is given, also returns `None` when the
        vehicle exists but doesn't belong to that customer."""
        ...

    def get_trip(self, trip_id: int, customer_id: int | None = None) -> Trip | None:
        """Return the `Trip` with `trip_id`, or `None` if it doesn't exist.
        If `customer_id` is given, also returns `None` when the trip exists
        but its driver doesn't belong to that customer."""
        ...

    def get_trips_for_driver(
        self, driver_id: int, customer_id: int | None = None
    ) -> list[Trip]:
        """Return all `Trip`s belonging to `driver_id`, ordered oldest-first
        (by `trip_id`). Empty list if the driver has no trips (or doesn't
        exist) -- not an error, matching the other list-returning methods
        here. If `customer_id` is given, also returns an empty list when
        `driver_id` doesn't belong to that customer."""
        ...

    def get_driving_events(
        self, trip_id: int, customer_id: int | None = None
    ) -> list[DrivingEvent]:
        """Return all `DrivingEvent`s recorded for `trip_id`, ordered
        chronologically (by `event_time`). Empty list if the trip has no
        events (or doesn't exist). If `customer_id` is given, also returns
        an empty list when the trip's driver doesn't belong to that
        customer."""
        ...

    def get_knowledge_base_articles(
        self, category: str | None = None
    ) -> list[KnowledgeBaseArticle]:
        """Return knowledge-base articles (the RAG corpus), optionally
        filtered to a single `category`. `category=None` (the default)
        returns the full corpus."""
        ...
