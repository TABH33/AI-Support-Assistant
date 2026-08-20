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

Scope note (revised after Task 10 review; signature tightened again by
final-review Fix 8): the ID-keyed methods (`get_driver`, `get_vehicle`,
`get_trip`, `get_trips_for_driver`, `get_driving_events`) accept a
REQUIRED keyword-only `customer_id: int | None`. This is *not* RBAC/JWT
re-entering the interface -- it's a plain `int`, the same primitive
`app/repositories/chat.py` (Task 8) already threads through its own
functions (e.g. `create_support_ticket(db, chat_session_id, customer_id,
...)`). The interface still doesn't know about `CurrentUser`, roles, or
tokens; it only knows "filter to rows reachable from this customer_id, or
don't, if `None` is passed explicitly."

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
DrivingEvent scoped transitively through Driver). Passing `customer_id=None`
explicitly keeps unscoped behavior available -- needed for contexts with no
single owning customer (e.g. a support_agent's cross-fleet view, matching
Task 7's own `support_agent`-is-unrestricted rule) -- and keeps a
hypothetical `DatabricksDataSource` free to ignore the parameter if its own
security model handles tenant isolation elsewhere (e.g. row-level security
enforced by the warehouse itself).

Final-review Fix 8: `customer_id: int | None = None` used to be a
DEFAULTED, fail-open parameter -- every current caller happened to pass it
correctly, but a future caller that forgot to would silently get unscoped,
cross-tenant-capable data with no error, rather than being forced to make
an explicit choice. Dropping the default (while keeping the type itself
nullable, since a genuine unscoped lookup is still a legitimate, deliberate
choice for e.g. a `support_agent` caller) makes every call site name its
intent -- `customer_id=None` for "deliberately unscoped" reads identically
different from simply forgetting the argument, which is now a `TypeError`
at the call site instead of a silent cross-tenant leak. `get_knowledge_base_
articles` has no `customer_id` parameter at all -- the knowledge base is
global, shared across all customers, not tenant-scoped data.

Fleet-wide listing methods (added for Task 16): `list_drivers`,
`list_vehicles`, `list_devices`, and `list_trips_for_customer` answer "give
me this customer's whole fleet", not "give me the one row with this id" --
a shape the ID-keyed methods above don't cover. Task 16's daily reports
(start-of-day / end-of-day summaries) need exactly this: every driver,
vehicle, device, and trip belonging to one customer, not a single resolved
entity. Unlike the ID-keyed methods, `customer_id` here is a required
positional parameter, not an optional defense-in-depth extra -- there is no
meaningful "list every driver across every customer" call site in this
plan (that's not what any report or endpoint needs), so making it required
removes the possibility of a call site accidentally omitting it and getting
an unscoped, cross-tenant result. Each mirrors the exact query shape its
`_scoped_*` counterpart in `app/api/telematics.py` (Task 7) already uses:
`list_drivers`/`list_vehicles`/`list_devices` filter directly on their own
`customer_id` column; `list_trips_for_customer` joins to `Driver` and
filters there, exactly like `_scoped_trips`. `since`/`until` (both
optional, both compared against `Trip.start_time`) let callers narrow to a
time window (e.g. "today") entirely in SQL, the same real-filtering
standard the ID-keyed methods already hold to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.device import Device
from app.models.knowledge import KnowledgeBaseArticle
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle


@runtime_checkable
class TelematicsDataSource(Protocol):
    """Read-only access to telematics + knowledge-base data, independent of
    the underlying storage engine."""

    def get_driver(self, driver_id: int, *, customer_id: int | None) -> Driver | None:
        """Return the `Driver` with `driver_id`, or `None` if it doesn't
        exist. `customer_id` is required (though its VALUE may be `None` for
        a deliberate unscoped lookup, e.g. a `support_agent` caller) -- see
        module docstring's final-review Fix 8 note. If a real `customer_id`
        is given, also returns `None` when the driver exists but doesn't
        belong to that customer (tenant-scoping, recommended whenever
        `driver_id` came from user-supplied/LLM-resolved input)."""
        ...

    def get_vehicle(self, vehicle_id: int, *, customer_id: int | None) -> Vehicle | None:
        """Return the `Vehicle` with `vehicle_id`, or `None` if it doesn't
        exist. `customer_id` is required (see `get_driver`'s docstring). If a
        real `customer_id` is given, also returns `None` when the vehicle
        exists but doesn't belong to that customer."""
        ...

    def get_trip(self, trip_id: int, *, customer_id: int | None) -> Trip | None:
        """Return the `Trip` with `trip_id`, or `None` if it doesn't exist.
        `customer_id` is required (see `get_driver`'s docstring). If a real
        `customer_id` is given, also returns `None` when the trip exists but
        its driver doesn't belong to that customer."""
        ...

    def get_trips_for_driver(
        self, driver_id: int, *, customer_id: int | None
    ) -> list[Trip]:
        """Return all `Trip`s belonging to `driver_id`, ordered oldest-first
        (by `trip_id`). Empty list if the driver has no trips (or doesn't
        exist) -- not an error, matching the other list-returning methods
        here. `customer_id` is required (see `get_driver`'s docstring). If a
        real `customer_id` is given, also returns an empty list when
        `driver_id` doesn't belong to that customer."""
        ...

    def get_driving_events(
        self, trip_id: int, *, customer_id: int | None
    ) -> list[DrivingEvent]:
        """Return all `DrivingEvent`s recorded for `trip_id`, ordered
        chronologically (by `event_time`). Empty list if the trip has no
        events (or doesn't exist). `customer_id` is required (see
        `get_driver`'s docstring). If a real `customer_id` is given, also
        returns an empty list when the trip's driver doesn't belong to that
        customer."""
        ...

    def get_knowledge_base_articles(
        self, category: str | None = None
    ) -> list[KnowledgeBaseArticle]:
        """Return knowledge-base articles (the RAG corpus), optionally
        filtered to a single `category`. `category=None` (the default)
        returns the full corpus."""
        ...

    def list_drivers(self, customer_id: int) -> list[Driver]:
        """Return every `Driver` belonging to `customer_id`, ordered by
        `driver_id`. Empty list if the customer has no drivers."""
        ...

    def list_vehicles(self, customer_id: int) -> list[Vehicle]:
        """Return every `Vehicle` belonging to `customer_id`, ordered by
        `vehicle_id`. Empty list if the customer has no vehicles."""
        ...

    def list_devices(self, customer_id: int) -> list[Device]:
        """Return every `Device` belonging to `customer_id`, ordered by
        `device_id`. Empty list if the customer has no devices."""
        ...

    def list_trips_for_customer(
        self,
        customer_id: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Trip]:
        """Return every `Trip` belonging to `customer_id` (scoped
        transitively via the trip's driver, same join `get_trip`/
        `get_trips_for_driver` use), ordered oldest-first (by `trip_id`).
        `since`/`until`, when given, filter to trips whose `start_time` is
        `>= since` / `< until` respectively (both real SQL `WHERE` clauses,
        not post-fetch Python filtering) -- e.g. a caller can pass today's
        UTC midnight as `since` and tomorrow's UTC midnight as `until` to
        get just today's trips. Empty list if nothing matches."""
        ...
