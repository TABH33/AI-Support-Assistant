# Simulated Live Route Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer watch their active route plans move along their own geometry, and a manager see every driver currently out on a route, by computing a simulated live position for each `active` `RoutePlan` on every read and rendering it on a new `/tracking` page.

**Architecture:** One nullable `driver_id` FK is added to `route_plans` (set once at `POST /route-plan` time), and a new pure module `backend/app/ai/route_tracking.py` derives `(current_lat, current_lon, progress_percent, eta)` from `(created_at, duration_min, geometry, now)` using the same `haversine_distance_km` helper `route_planning.sample_route_points` already uses. A new `GET /route-plans/live` endpoint reuses `GET /route-plans`' RBAC/scoping, filters to `status=active AND unavailable=false`, joins `Driver` for a display name, and returns the computed fields; the frontend adds a driver dropdown to the plan form plus a `/tracking` page that polls that endpoint every 5 seconds and draws all tracked routes on one Leaflet map.

**Tech Stack:** FastAPI + SQLAlchemy 2.x + Alembic (backend, Python 3.11+), React 18 + TypeScript + Vite + Tailwind + react-leaflet 4.2.1 (frontend), pytest + Vitest/Testing Library (tests). No new dependencies.

**Spec:** docs/superpowers/specs/2026-09-24-live-tracking-design.md

## Global Constraints

- **No real GPS/device integration.** No GPS or device hardware exists anywhere in this codebase (all telematics data is synthetic/seeded). "Real-time" here means the backend computes a position along the route's already-known geometry, proportional to elapsed time since the plan was created. This is the intended design for this POC, **not** a placeholder to be swapped for a real integration.
- **Position is never stored.** It is a pure function of `(created_at, duration_min, geometry, now)`, recomputed on every read. No background job/scheduler (this app has none), nothing to keep in sync, and it survives a backend restart for free.
- **No WebSocket/push delivery** — polling only, per the approved design.
- **No historical position trail/replay** — only the current live position is shown; nothing is stored over time.
- **No driver reassignment after a route is created.** `driver_id` is set once, at `POST /route-plan` time, matching how origin/destination are also immutable after creation per the existing route-selector design.
- **`driver_id` is optional.** A route plan with no assigned driver still tracks, shown as "Unassigned".
- **Schema change is exactly one nullable column** (`route_plans.driver_id`, FK → `drivers.driver_id`) in a new Alembic migration chaining onto the current head `7c3e9a2f1b44`. No other schema changes.
- `GET /route-plans/live` uses the **same RBAC/scoping as `GET /route-plans`**: `customer` → own rows only (any `?customer_id=` ignored); `support_agent` → all customers, or narrowed by `?customer_id=`.
- **No new frontend routing/state-management dependency** — reuses `react-router-dom` (already in use) and plain `useEffect`/`setInterval` polling, the same pattern every other page already uses for its initial fetch.
- `eta` is **fixed** at `created_at + duration_min` — it does not move as time passes, matching how `Routes.tsx`'s existing "Estimated arrival" already works.
- Helper functions that write rows **flush, never commit** — the request handler owns the single `db.commit()` (same convention as `save_route_plan`/`record_audit_event`).

---

## Task 1: `RoutePlan.driver_id` column + Alembic migration

**Files:**
- Modify: `backend/app/models/route_plan.py` (imports at lines 18-26, column block at lines 35-54)
- Create: `backend/alembic/versions/3d5f81ac9e62_add_driver_id_to_route_plans.py`
- Test: `backend/tests/test_route_plan_model.py` (append)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `RoutePlan.driver_id: Mapped[int | None]` (SQLAlchemy column, FK `drivers.driver_id`, nullable, default `None`); Alembic revision `3d5f81ac9e62`, `down_revision='7c3e9a2f1b44'` (verified current head: the chain is `482fedba36a5 → f047842cb6ff → 054e88c6af09 → c7fbb78e2f74 → ca294b499e12 → 6985ed4f7a05 → f8e6b5aa61ed → 7c3e9a2f1b44`, and no existing revision points past it).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_route_plan_model.py` (the file's existing `engine`/`db_session`/`_make_customer` fixtures are reused as-is; add `Driver` to the existing `from app.models import ...` line so it reads `from app.models import Base, Customer, Driver, RoutePlan`):

```python
def _make_driver(db_session: Session, *, customer: Customer) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name="Model Test Driver",
        license_number="LIC-MODEL-0001",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def test_route_plan_driver_id_defaults_to_none(db_session):
    customer = _make_customer(db_session)
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        unavailable=False,
        warnings=[],
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)

    assert route_plan.driver_id is None


def test_route_plan_round_trips_an_assigned_driver_id(db_session):
    customer = _make_customer(db_session)
    driver = _make_driver(db_session, customer=customer)
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        driver_id=driver.driver_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        unavailable=False,
        warnings=[],
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)

    assert route_plan.driver_id == driver.driver_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_route_plan_model.py -v`

Expected: both new tests fail. `test_route_plan_driver_id_defaults_to_none` fails with `AttributeError: 'RoutePlan' object has no attribute 'driver_id'`, and `test_route_plan_round_trips_an_assigned_driver_id` fails with `TypeError: 'driver_id' is an invalid keyword argument for RoutePlan`. The two pre-existing tests in the file still pass.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/models/route_plan.py`, change the `TYPE_CHECKING` import block so it reads:

```python
if TYPE_CHECKING:
    from app.models.customer import Customer
```

(unchanged — `Driver` is referenced only by FK string, no Python-level relationship is added; the live endpoint joins explicitly, mirroring `app/api/telematics.py`'s explicit-join style.)

Add the column immediately after `created_by_id` (i.e. between the `created_by_id` line and the `origin_label` line):

```python
    # Optional: a route plan with no assigned driver still tracks (shown as
    # "Unassigned" by the live-tracking UI). Set once, at POST /route-plan
    # time -- there is deliberately no reassignment path, matching how
    # origin/destination are also immutable after creation. See
    # docs/superpowers/specs/2026-09-24-live-tracking-design.md.
    driver_id: Mapped[int | None] = mapped_column(
        ForeignKey("drivers.driver_id"), nullable=True
    )
```

Create `backend/alembic/versions/3d5f81ac9e62_add_driver_id_to_route_plans.py`:

```python
"""add_driver_id_to_route_plans

Revision ID: 3d5f81ac9e62
Revises: 7c3e9a2f1b44
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d5f81ac9e62'
down_revision: Union[str, Sequence[str], None] = '7c3e9a2f1b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Batch mode, same reason as 054e88c6af09: SQLite (the dev/test fallback
    # used when a live Postgres+pgvector instance isn't available) has no
    # ALTER TABLE ADD CONSTRAINT support, so adding a FK to an
    # already-existing table requires Alembic's copy-and-move batch
    # strategy. Batch mode is a passthrough on Postgres (`recreate="auto"`
    # only recreates on SQLite), so this runs unchanged there.
    with op.batch_alter_table('route_plans') as batch_op:
        batch_op.add_column(sa.Column('driver_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_route_plans_driver_id_drivers', 'drivers', ['driver_id'], ['driver_id']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('route_plans') as batch_op:
        batch_op.drop_constraint('fk_route_plans_driver_id_drivers', type_='foreignkey')
        batch_op.drop_column('driver_id')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_route_plan_model.py -v`

Expected: all 4 tests in the file pass (2 pre-existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/route_plan.py backend/alembic/versions/3d5f81ac9e62_add_driver_id_to_route_plans.py backend/tests/test_route_plan_model.py
git commit -m "Add nullable driver_id to route_plans"
```

---

## Task 2: `app/ai/route_tracking.py` — `interpolate_position` / `compute_route_progress`

**Files:**
- Create: `backend/app/ai/route_tracking.py`
- Test: `backend/tests/test_route_tracking.py`

**Interfaces:**
- Consumes: `RoutePlan.driver_id` is not needed here; uses `RoutePlan.created_at`, `RoutePlan.duration_min`, `RoutePlan.geometry` (Task 1's model), and `haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float` from `app/geo.py`.
- Produces:
  - `RouteProgress` — frozen dataclass with fields `current_lat: float`, `current_lon: float`, `progress_percent: float`, `eta: datetime`.
  - `interpolate_position(geometry: dict, fraction: float) -> tuple[float, float]` — returns `(latitude, longitude)`; raises `ValueError` when the geometry carries no coordinates.
  - `compute_route_progress(route_plan: RoutePlan, *, now: datetime) -> RouteProgress`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_route_tracking.py`:

```python
"""Tests for app.ai.route_tracking -- the simulated live-position maths.

Pure unit tests: no DB, no FastAPI, no network. `RoutePlan` instances are
constructed in memory (never added to a session) since nothing here reads
or writes the database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai.route_tracking import RouteProgress, compute_route_progress, interpolate_position
from app.models.route_plan import RoutePlan

#: Due-south meridian line: longitude constant, latitude -33.0 -> -34.0.
#: Great-circle distance along a meridian is linear in latitude, so the
#: expected interpolated latitude for any fraction is exact to float
#: precision -- which is what makes these assertions deterministic.
_MERIDIAN_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[151.0, -33.0], [151.0, -34.0]],
}

#: Same start/end, but with an unevenly-spaced intermediate vertex. Index-
#: based interpolation would put the halfway point at [151.0, -33.2];
#: distance-based interpolation (what this module does) puts it at -33.5.
_UNEVEN_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[151.0, -33.0], [151.0, -33.2], [151.0, -34.0]],
}

_FIXED_NOW = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)


def _route_plan(*, created_at: datetime, duration_min: float | None, geometry: dict | None):
    return RoutePlan(
        customer_id=1,
        created_by_role="customer",
        created_by_id=1,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=10.0,
        duration_min=duration_min,
        geometry=geometry,
        warnings=[],
        unavailable=False,
        created_at=created_at,
    )


def test_interpolate_position_at_zero_returns_the_first_coordinate():
    assert interpolate_position(_MERIDIAN_GEOMETRY, 0.0) == (-33.0, 151.0)


def test_interpolate_position_at_one_returns_the_last_coordinate():
    assert interpolate_position(_MERIDIAN_GEOMETRY, 1.0) == (-34.0, 151.0)


def test_interpolate_position_at_half_returns_the_midpoint():
    latitude, longitude = interpolate_position(_MERIDIAN_GEOMETRY, 0.5)

    assert latitude == pytest.approx(-33.5, abs=1e-6)
    assert longitude == pytest.approx(151.0, abs=1e-6)


def test_interpolate_position_is_distance_weighted_not_index_weighted():
    latitude, _ = interpolate_position(_UNEVEN_GEOMETRY, 0.5)

    assert latitude == pytest.approx(-33.5, abs=1e-6)


def test_interpolate_position_returns_the_only_point_of_a_single_point_line():
    geometry = {"type": "LineString", "coordinates": [[151.0, -33.0]]}

    assert interpolate_position(geometry, 0.5) == (-33.0, 151.0)


def test_interpolate_position_rejects_a_geometry_with_no_coordinates():
    with pytest.raises(ValueError):
        interpolate_position({"type": "LineString", "coordinates": []}, 0.5)


def test_compute_route_progress_at_creation_is_zero_percent_at_the_origin():
    route_plan = _route_plan(
        created_at=_FIXED_NOW, duration_min=60.0, geometry=_MERIDIAN_GEOMETRY
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert isinstance(progress, RouteProgress)
    assert progress.progress_percent == pytest.approx(0.0, abs=1e-6)
    assert progress.current_lat == pytest.approx(-33.0, abs=1e-6)
    assert progress.current_lon == pytest.approx(151.0, abs=1e-6)


def test_compute_route_progress_halfway_through_is_fifty_percent_at_the_midpoint():
    route_plan = _route_plan(
        created_at=_FIXED_NOW - timedelta(minutes=30),
        duration_min=60.0,
        geometry=_MERIDIAN_GEOMETRY,
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert progress.progress_percent == pytest.approx(50.0, abs=1e-6)
    assert progress.current_lat == pytest.approx(-33.5, abs=1e-6)


def test_compute_route_progress_at_the_full_duration_is_one_hundred_percent():
    route_plan = _route_plan(
        created_at=_FIXED_NOW - timedelta(minutes=60),
        duration_min=60.0,
        geometry=_MERIDIAN_GEOMETRY,
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert progress.progress_percent == pytest.approx(100.0, abs=1e-6)
    assert progress.current_lat == pytest.approx(-34.0, abs=1e-6)


def test_compute_route_progress_past_the_full_duration_stays_at_one_hundred_percent():
    route_plan = _route_plan(
        created_at=_FIXED_NOW - timedelta(minutes=600),
        duration_min=60.0,
        geometry=_MERIDIAN_GEOMETRY,
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert progress.progress_percent == pytest.approx(100.0, abs=1e-6)
    assert progress.current_lat == pytest.approx(-34.0, abs=1e-6)


def test_compute_route_progress_before_creation_clamps_to_zero_percent():
    route_plan = _route_plan(
        created_at=_FIXED_NOW + timedelta(minutes=5),
        duration_min=60.0,
        geometry=_MERIDIAN_GEOMETRY,
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert progress.progress_percent == pytest.approx(0.0, abs=1e-6)
    assert progress.current_lat == pytest.approx(-33.0, abs=1e-6)


def test_compute_route_progress_eta_is_fixed_at_created_at_plus_duration():
    created_at = _FIXED_NOW - timedelta(minutes=30)
    route_plan = _route_plan(
        created_at=created_at, duration_min=60.0, geometry=_MERIDIAN_GEOMETRY
    )

    early = compute_route_progress(route_plan, now=_FIXED_NOW)
    later = compute_route_progress(route_plan, now=_FIXED_NOW + timedelta(minutes=20))

    assert early.eta == created_at + timedelta(minutes=60)
    assert later.eta == early.eta


def test_compute_route_progress_treats_a_naive_created_at_as_utc():
    """SQLite returns tz-naive datetimes for a DateTime(timezone=True)
    column, so a row read back from the test/dev database has no tzinfo --
    subtracting an aware `now` from it would raise TypeError."""
    route_plan = _route_plan(
        created_at=datetime(2026, 9, 24, 9, 30, 0),
        duration_min=60.0,
        geometry=_MERIDIAN_GEOMETRY,
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert progress.progress_percent == pytest.approx(50.0, abs=1e-6)


def test_compute_route_progress_treats_a_missing_duration_as_arrived():
    route_plan = _route_plan(
        created_at=_FIXED_NOW, duration_min=None, geometry=_MERIDIAN_GEOMETRY
    )

    progress = compute_route_progress(route_plan, now=_FIXED_NOW)

    assert progress.progress_percent == pytest.approx(100.0, abs=1e-6)
    assert progress.current_lat == pytest.approx(-34.0, abs=1e-6)
    assert progress.eta == _FIXED_NOW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_route_tracking.py -v`

Expected: collection fails at import time with `ModuleNotFoundError: No module named 'app.ai.route_tracking'` — all 14 tests error out.

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/ai/route_tracking.py`:

```python
"""Simulated live-position tracking for an active `RoutePlan`.

Pure functions -- no FastAPI imports, no database session, no network --
mirroring `app/ai/route_planning.py`'s own shape, and using the same
`haversine_distance_km` helper (`app/geo.py`) that
`route_planning.sample_route_points` uses, so a position computed here and
a warning distance computed there always agree about how far along a route
a point is.

Nothing here is persisted. A position is a pure function of
`(created_at, duration_min, geometry, now)`, recomputed on every read of
`GET /route-plans/live` -- which means no background job/scheduler (this
app has none), nothing to keep in sync, and it survives a backend restart
for free. There is no GPS or device hardware anywhere in this codebase:
the simulated movement below is the intended design for this POC, not a
placeholder for a later real integration. See
docs/superpowers/specs/2026-09-24-live-tracking-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.geo import haversine_distance_km
from app.models.route_plan import RoutePlan


@dataclass(frozen=True)
class RouteProgress:
    """Where a route plan's vehicle is simulated to be right now, and when
    it is expected to arrive. `progress_percent` is 0.0-100.0 inclusive
    (already clamped); `eta` is fixed at creation time and does not move as
    time passes."""

    current_lat: float
    current_lon: float
    progress_percent: float
    eta: datetime


def interpolate_position(geometry: dict, fraction: float) -> tuple[float, float]:
    """(latitude, longitude) at `fraction` (0.0-1.0, already clamped by the
    caller) of the way along a GeoJSON LineString's total distance.

    Walks the coordinate list (each entry `[longitude, latitude]`, the same
    GeoJSON order `sample_route_points` reads) accumulating
    `haversine_distance_km` between consecutive points, then linearly
    interpolates between the two coordinates the target distance falls
    between -- so the simulated vehicle moves at a constant speed along the
    real geometry, rather than jumping from vertex to vertex at a rate set
    by how densely the routing service happened to sample that stretch of
    road.

    Returns the only coordinate for a single-point line (nothing to
    interpolate along), and likewise for a degenerate line whose total
    length is zero. Raises ValueError when the geometry carries no
    coordinates at all -- callers must filter those rows out first, since
    there is no meaningful position to invent for them and returning
    (0.0, 0.0) would silently plot a vehicle in the Gulf of Guinea.
    """
    coordinates = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
    if not coordinates:
        raise ValueError("geometry has no coordinates to interpolate along")

    if len(coordinates) == 1:
        return coordinates[0][1], coordinates[0][0]

    segment_lengths_km = [
        haversine_distance_km(
            coordinates[i][1], coordinates[i][0], coordinates[i + 1][1], coordinates[i + 1][0]
        )
        for i in range(len(coordinates) - 1)
    ]
    total_km = sum(segment_lengths_km)
    if total_km <= 0:
        return coordinates[0][1], coordinates[0][0]

    target_km = total_km * fraction
    travelled_km = 0.0
    for index, segment_km in enumerate(segment_lengths_km):
        if travelled_km + segment_km >= target_km:
            # Position within this segment, as a 0.0-1.0 share of its own
            # length. A zero-length segment (duplicate consecutive
            # coordinates, which ORS does emit) can only be reached when
            # target_km == travelled_km, so its start point is the answer.
            segment_fraction = (target_km - travelled_km) / segment_km if segment_km > 0 else 0.0
            start_lon, start_lat = coordinates[index][0], coordinates[index][1]
            end_lon, end_lat = coordinates[index + 1][0], coordinates[index + 1][1]
            return (
                start_lat + (end_lat - start_lat) * segment_fraction,
                start_lon + (end_lon - start_lon) * segment_fraction,
            )
        travelled_km += segment_km

    # Floating-point accumulation can leave the loop above a hair short of
    # target_km at fraction == 1.0; the destination is the right answer.
    return coordinates[-1][1], coordinates[-1][0]


def compute_route_progress(route_plan: RoutePlan, *, now: datetime) -> RouteProgress:
    """The simulated live position, progress and ETA of one `RoutePlan`.

    `progress_percent` is `clamp((now - created_at) / duration_min, 0.0,
    1.0) * 100`, using minutes on both sides. `eta` is `created_at +
    duration_min` -- fixed, so it does not slide forward as time passes,
    matching how `Routes.tsx`'s existing "Estimated arrival" already works.
    `current_lat`/`current_lon` come from
    `interpolate_position(geometry, progress_percent / 100)`.

    A missing or non-positive `duration_min` is treated as 100% complete
    (there is no travel time left to simulate), which also keeps the
    division below safe. `created_at` read back from SQLite is tz-naive
    even though the column is `DateTime(timezone=True)`, so it is
    normalized to UTC before any arithmetic with an aware `now`.

    Raises ValueError (via `interpolate_position`) for a plan whose
    geometry has no coordinates -- the caller filters those rows out.
    """
    created_at = route_plan.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    # Numeric(10, 2) reads back as Decimal; float() matches how
    # `_row_to_list_item` in app/api/route_plan.py already normalizes it.
    duration_min = float(route_plan.duration_min) if route_plan.duration_min is not None else 0.0

    if duration_min <= 0:
        fraction = 1.0
    else:
        elapsed_min = (now - created_at).total_seconds() / 60.0
        fraction = min(max(elapsed_min / duration_min, 0.0), 1.0)

    latitude, longitude = interpolate_position(route_plan.geometry or {}, fraction)

    return RouteProgress(
        current_lat=latitude,
        current_lon=longitude,
        progress_percent=fraction * 100.0,
        eta=created_at + timedelta(minutes=duration_min),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_route_tracking.py -v`

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/route_tracking.py backend/tests/test_route_tracking.py
git commit -m "Add simulated route-position interpolation and progress maths"
```

---

## Task 3: Accept `driver_id` on `POST /route-plan`

**Files:**
- Modify: `backend/app/ai/route_planning.py` (`save_route_plan`, lines 365-412)
- Modify: `backend/app/api/route_plan.py` (imports lines 25-32, `RoutePlanRequest` lines 44-53, helpers after `_resolve_customer_id` at lines 142-150, `post_route_plan` lines 171-207)
- Modify: `backend/app/api/chat.py` (the `save_route_plan(...)` call at lines 418-426)
- Test: `backend/tests/test_route_plan_api.py` (append)

**Interfaces:**
- Consumes: `RoutePlan.driver_id` (Task 1).
- Produces:
  - `save_route_plan(db: Session, result: RoutePlanResult, *, customer_id: int, created_by_role: str, created_by_id: int, origin_label: str, destination_label: str, driver_id: int | None = None) -> RoutePlan` — every later call site must use this exact signature.
  - `RoutePlanRequest.driver_id: int | None = None`.
  - `_resolve_driver_id(payload: RoutePlanRequest, *, customer_id: int, db: Session) -> int | None` (module-private helper in `app/api/route_plan.py`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_route_plan_api.py` (the file's existing `engine`/`db_session`/`client`/`customer_headers` fixtures and `_GEOMETRY` constant are reused as-is; add `Driver` to the existing `from app.models import AuditLog, Base, Customer, RoutePlan` line so it reads `from app.models import AuditLog, Base, Customer, Driver, RoutePlan`):

```python
# ---------------------------------------------------------------------------
# Live tracking: POST /route-plan accepts an optional driver_id, validated
# against the resolved customer's own fleet.
# ---------------------------------------------------------------------------


@pytest.fixture()
def driver_customer(db_session):
    """A customer returned as the row itself (rather than only headers, as
    `customer_headers` above does) so a Driver can be created in its fleet
    and its id asserted against the saved RoutePlan."""
    customer = Customer(
        full_name="Driver Fleet Customer",
        email="driver-fleet-customer@example.test",
        phone_number="+61000000003",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


@pytest.fixture()
def driver_customer_headers(driver_customer):
    token = create_access_token(subject=driver_customer.customer_id, role="customer")
    return {"Authorization": f"Bearer {token}"}


def _make_driver(db_session, *, customer_id: int, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer_id,
        full_name=f"Fleet Driver {tag}",
        license_number=f"LIC-ROUTE-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def test_route_plan_accepts_a_driver_from_the_callers_own_fleet(
    client, db_session, driver_customer, driver_customer_headers
):
    driver = _make_driver(db_session, customer_id=driver_customer.customer_id, tag="OWN")
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "driver_id": driver.driver_id,
            },
            headers=driver_customer_headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.driver_id == driver.driver_id


def test_route_plan_rejects_a_driver_from_another_customers_fleet(
    client, db_session, driver_customer, driver_customer_headers
):
    other_customer = Customer(
        full_name="Other Fleet Customer",
        email="other-fleet-customer@example.test",
        phone_number="+61000000004",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(other_customer)
    db_session.commit()
    db_session.refresh(other_customer)
    other_driver = _make_driver(db_session, customer_id=other_customer.customer_id, tag="OTHER")

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "driver_id": other_driver.driver_id,
            },
            headers=driver_customer_headers,
        )

    assert response.status_code == 400
    assert "driver_id" in response.json()["detail"]
    assert db_session.query(RoutePlan).count() == 0


def test_route_plan_rejects_a_nonexistent_driver_id(client, db_session, driver_customer_headers):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta", "driver_id": 999999},
            headers=driver_customer_headers,
        )

    assert response.status_code == 400
    assert "driver_id" in response.json()["detail"]
    assert db_session.query(RoutePlan).count() == 0


def test_route_plan_without_a_driver_id_saves_an_unassigned_plan(
    client, db_session, driver_customer_headers
):
    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=driver_customer_headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.driver_id is None


def test_support_agent_driver_is_validated_against_the_named_customer(client, db_session):
    customer = Customer(
        full_name="Agent Driver Customer",
        email="agent-driver-customer@example.test",
        phone_number="+61000000005",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    driver = _make_driver(db_session, customer_id=customer.customer_id, tag="AGENT")

    agent_token = create_access_token(subject=99, role="support_agent")
    headers = {"Authorization": f"Bearer {agent_token}"}

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "customer_id": customer.customer_id,
                "driver_id": driver.driver_id,
            },
            headers=headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.driver_id == driver.driver_id
    assert saved.customer_id == customer.customer_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_route_plan_api.py -v`

Expected: the 5 new tests fail. `test_route_plan_accepts_a_driver_from_the_callers_own_fleet` fails on `assert saved.driver_id == driver.driver_id` (it is `None` — `RoutePlanRequest` has no `driver_id`, so the field is dropped by Pydantic and never persisted), and the two rejection tests fail with `assert 200 == 400`. The 12 pre-existing tests in the file still pass.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/ai/route_planning.py`, replace `save_route_plan`'s signature and docstring and add the column assignment, so the function reads:

```python
def save_route_plan(
    db: Session,
    result: RoutePlanResult,
    *,
    customer_id: int,
    created_by_role: str,
    created_by_id: int,
    origin_label: str,
    destination_label: str,
    driver_id: int | None = None,
) -> RoutePlan:
    """Persists `result` as a `RoutePlan` row so it can later be listed
    (`GET /route-plans`), tracked live (`GET /route-plans/live`), or
    summarized for the day it was created (`summarize_todays_routes`
    below). Called from both `POST /route-plan` and the chat route-plan
    intent (`app/api/chat.py`) so a route planned through either surface is
    tracked identically -- including a failed plan
    (`result.unavailable=True`), which is still worth recording (a manager
    asking "any routes fail to plan today?" needs this).

    `driver_id` is optional and defaults to None ("Unassigned"): the chat
    route-plan intent has no driver-selection surface, and the route form
    itself leaves it blank unless a driver is picked. Callers are
    responsible for validating that the driver belongs to `customer_id`'s
    fleet before passing it (see `_resolve_driver_id` in
    `app/api/route_plan.py`) -- this function does no lookup of its own.

    Flushes, does not commit -- same single-transaction-per-request
    convention as `record_audit_event` (see `app.security.audit`); the
    caller owns `db.commit()`."""
    route_plan = RoutePlan(
        customer_id=customer_id,
        created_by_role=created_by_role,
        created_by_id=created_by_id,
        driver_id=driver_id,
        origin_label=origin_label,
        destination_label=destination_label,
        distance_km=result.distance_km,
        duration_min=result.duration_min,
        geometry=result.geometry,
        warnings=[
            {
                "location": {"lat": w.latitude, "lon": w.longitude},
                "distance_from_origin_km": w.distance_from_origin_km,
                "type": w.type,
                "severity": w.severity,
                "description": w.description,
            }
            for w in result.warnings
        ],
        unavailable=result.unavailable,
        unavailable_reason=result.unavailable_reason,
        status=RoutePlanStatus.ACTIVE,
    )
    db.add(route_plan)
    db.flush()
    db.refresh(route_plan)
    return route_plan
```

In `backend/app/api/route_plan.py`, add the `Driver` import immediately after the existing `from app.models.enums import RoutePlanStatus` line:

```python
from app.models.telematics import Driver
```

Add the field to `RoutePlanRequest`, immediately after the existing `customer_id` field:

```python
    #: Optional driver assignment for live tracking. Validated against the
    #: RESOLVED customer's fleet (see `_resolve_driver_id`) -- a
    #: `support_agent` planning on a customer's behalf must pass a driver
    #: from that customer's fleet, not their own. Set once, here: there is
    #: deliberately no reassignment endpoint. See
    #: docs/superpowers/specs/2026-09-24-live-tracking-design.md.
    driver_id: int | None = None
```

Add this helper immediately after `_resolve_customer_id`:

```python
def _resolve_driver_id(payload: RoutePlanRequest, *, customer_id: int, db: Session) -> int | None:
    """Validate that `payload.driver_id` names a `Driver` in `customer_id`'s
    own fleet, returning it (or None when no driver was given -- an
    unassigned plan still tracks).

    400, not 404: this is a malformed field on a write, so the request
    itself is rejected rather than a resource being reported missing. A
    nonexistent driver id and another customer's driver id are
    deliberately indistinguishable in the response, for the same "never
    reveal that a row exists but isn't yours" reason the read endpoints use
    404-not-403 (see `app/api/telematics.py`'s module docstring)."""
    if payload.driver_id is None:
        return None

    driver = (
        db.query(Driver)
        .filter(Driver.driver_id == payload.driver_id, Driver.customer_id == customer_id)
        .one_or_none()
    )
    if driver is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="driver_id does not name a driver in this customer's fleet",
        )
    return driver.driver_id
```

In `post_route_plan`, replace the body from the `customer_id = ...` line through the `save_route_plan(...)` call with:

```python
    customer_id = _resolve_customer_id(payload, current_user)
    driver_id = _resolve_driver_id(payload, customer_id=customer_id, db=db)

    origin = _to_origin_input(payload.origin)
    destination = _to_origin_input(payload.destination)
    waypoints = [_to_origin_input(w) for w in payload.waypoints] if payload.waypoints else None

    result = build_route_plan(origin, destination, waypoints, db=db)

    route_plan_row = save_route_plan(
        db,
        result,
        customer_id=customer_id,
        created_by_role=current_user.role,
        created_by_id=current_user.user_id,
        origin_label=_to_place_label(payload.origin),
        destination_label=_to_place_label(payload.destination),
        driver_id=driver_id,
    )
```

In `backend/app/api/chat.py`, replace the `save_route_plan(...)` call with the explicit-`None` form:

```python
            saved_route_plan = save_route_plan(
                db,
                route_result,
                customer_id=customer_id,
                created_by_role=current_user.role,
                created_by_id=current_user.user_id,
                origin_label=route_plan_intent.origin,
                destination_label=route_plan_intent.destination,
                # The chat route-plan intent has no driver-selection
                # surface -- a route planned in chat is always saved
                # unassigned, and there is no reassignment path afterwards.
                # Passed explicitly rather than relying on the default so
                # this stays a deliberate choice, not an oversight.
                driver_id=None,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_route_plan_api.py tests/test_chat_api.py -v`

Expected: all 17 tests in `test_route_plan_api.py` pass (12 pre-existing + 5 new) and the whole `test_chat_api.py` suite still passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/route_planning.py backend/app/api/route_plan.py backend/app/api/chat.py backend/tests/test_route_plan_api.py
git commit -m "Accept an optional fleet-validated driver_id on POST /route-plan"
```

---

## Task 4: `GET /route-plans/live` endpoint

**Files:**
- Modify: `backend/app/api/route_plan.py` (module docstring lines 1-16, imports lines 17-33, schemas after `RoutePlanListItem` at lines 82-96, helpers after `_row_to_list_item` at lines 153-168, new route after `get_route_plans` at lines 210-233)
- Test: `backend/tests/test_route_plans_live_api.py`

**Interfaces:**
- Consumes: `compute_route_progress(route_plan: RoutePlan, *, now: datetime) -> RouteProgress` and `RouteProgress(current_lat, current_lon, progress_percent, eta)` (Task 2); `RoutePlan.driver_id` (Task 1).
- Produces: `GET /route-plans/live?customer_id=<int>` → `list[LiveRoutePlan]`, where `LiveRoutePlan(RoutePlanListItem)` adds `driver_id: int | None`, `driver_name: str | None`, `current_lat: float`, `current_lon: float`, `progress_percent: float`, `eta: datetime`. This is the exact JSON shape Tasks 6 and 7 consume.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_route_plans_live_api.py`:

```python
"""Tests for `GET /route-plans/live`: role-based scoping, the
active-and-available-only filter, and the computed live-position fields.

Fixture pattern copied from test_route_plans_list_api.py (same
engine/db_session/client trio, same in-memory SQLite `get_db` override).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, Customer, Driver, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
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


#: Due-south meridian line, same fixture geometry as test_route_tracking.py:
#: distance along a meridian is linear in latitude, so an exact expected
#: position can be asserted for any progress fraction.
_GEOMETRY = {"type": "LineString", "coordinates": [[151.0, -33.0], [151.0, -34.0]]}


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Live Customer {tag}",
        email=f"live-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_driver(db_session: Session, *, customer: Customer, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Live Driver {tag}",
        license_number=f"LIC-LIVE-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def _make_route_plan(
    db_session: Session,
    *,
    customer: Customer,
    status: RoutePlanStatus = RoutePlanStatus.ACTIVE,
    created_at: datetime | None = None,
    duration_min: float | None = 60.0,
    geometry: dict | None = None,
    unavailable: bool = False,
    driver_id: int | None = None,
) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        driver_id=driver_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=10.0,
        duration_min=duration_min,
        geometry=_GEOMETRY if geometry is None else geometry,
        warnings=[],
        unavailable=unavailable,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def test_unauthenticated_request_is_rejected(client):
    response = client.get("/route-plans/live")
    assert response.status_code == 401


def test_customer_sees_only_their_own_live_routes(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=customer_a.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_a.customer_id


def test_support_agent_sees_all_customers_when_unscoped(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_support_agent_can_narrow_by_customer_id(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get(
        f"/route-plans/live?customer_id={customer_b.customer_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_b.customer_id


def test_completed_routes_are_excluded(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer, status=RoutePlanStatus.COMPLETED)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_unavailable_routes_are_excluded(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(
        db_session, customer=customer, unavailable=True, duration_min=None, geometry={}
    )

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_routes_without_geometry_points_are_excluded(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(
        db_session, customer=customer, geometry={"type": "LineString", "coordinates": []}
    )

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == []


def test_progress_is_computed_from_elapsed_time(client, db_session):
    customer = _make_customer(db_session, tag="A")
    created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    _make_route_plan(db_session, customer=customer, created_at=created_at, duration_min=60.0)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert len(body) == 1
    # 30 of 60 minutes elapsed; a second or two of test runtime keeps this
    # just above 50%, never below.
    assert 50.0 <= body[0]["progress_percent"] < 51.0
    assert body[0]["current_lat"] == pytest.approx(-33.5, abs=0.01)
    assert body[0]["current_lon"] == pytest.approx(151.0, abs=1e-6)


def test_progress_past_the_duration_clamps_to_one_hundred_percent(client, db_session):
    customer = _make_customer(db_session, tag="A")
    created_at = datetime.now(timezone.utc) - timedelta(minutes=600)
    _make_route_plan(db_session, customer=customer, created_at=created_at, duration_min=60.0)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert body[0]["progress_percent"] == pytest.approx(100.0, abs=1e-6)
    assert body[0]["current_lat"] == pytest.approx(-34.0, abs=1e-6)


def test_assigned_driver_name_is_joined_in(client, db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, tag="A")
    _make_route_plan(db_session, customer=customer, driver_id=driver.driver_id)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert body[0]["driver_id"] == driver.driver_id
    assert body[0]["driver_name"] == "Live Driver A"


def test_unassigned_route_has_a_null_driver_name(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    body = response.json()
    assert body[0]["driver_id"] is None
    assert body[0]["driver_name"] is None


def test_response_keeps_the_list_item_fields(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans/live", headers={"Authorization": f"Bearer {token}"})

    row = response.json()[0]
    assert row["origin_label"] == "Sydney CBD"
    assert row["destination_label"] == "Parramatta"
    assert row["status"] == "active"
    assert row["geometry"] == _GEOMETRY
    assert row["warnings"] == []
    assert row["eta"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_route_plans_live_api.py -v`

Expected: `test_unauthenticated_request_is_rejected` fails with `assert 404 == 401` and every other test fails with `assert 404 == 200` (or a `KeyError`/`TypeError` reading fields off the 404 body) — the `/route-plans/live` route does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/api/route_plan.py`, extend the module docstring's first paragraph so it reads:

```python
"""`POST /route-plan` -- computes a route via OpenRouteService, evaluates
weather and historical-risk-zone warnings along it, persists the result as
a `RoutePlan` row, and returns one structured result.
`GET /route-plans` and `PATCH /route-plans/{id}/complete` (Tasks 3-4) list
and complete those saved rows. `GET /route-plans/live` returns the
currently-active ones with a simulated live position computed on the fly
(nothing is stored -- see `app/ai/route_tracking.py` and
docs/superpowers/specs/2026-09-24-live-tracking-design.md). See
docs/superpowers/specs/2026-08-26-route-planning-warnings-design.md and
docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.
```

(the RBAC paragraph that follows it is unchanged.)

Add the import immediately after the existing `from app.ai.route_planning import ...` block:

```python
from app.ai.route_tracking import compute_route_progress
```

Add this schema immediately after `RoutePlanListItem`:

```python
class LiveRoutePlan(RoutePlanListItem):
    """`GET /route-plans/live` row: everything `GET /route-plans` returns,
    plus the assigned driver and the simulated current position. The
    position fields are recomputed on every request from
    `(created_at, duration_min, geometry, now)` and are never persisted."""

    driver_id: int | None
    #: Joined from `Driver.full_name`; None when the plan is unassigned.
    driver_name: str | None
    current_lat: float
    current_lon: float
    progress_percent: float
    #: `created_at + duration_min` -- fixed, so it does not slide forward
    #: as time passes (same behavior as Routes.tsx's "Estimated arrival").
    eta: datetime
```

Add this helper immediately after `_row_to_list_item`:

```python
def _geometry_points(geometry: dict | None) -> list:
    """The GeoJSON LineString coordinate list of a saved plan, or `[]` when
    there is nothing to interpolate a position along (no geometry at all,
    or an empty/malformed `coordinates`). `GET /route-plans/live` skips
    those rows rather than inventing a position for them."""
    if not isinstance(geometry, dict):
        return []
    coordinates = geometry.get("coordinates")
    return coordinates if isinstance(coordinates, list) else []
```

Add this route immediately after `get_route_plans`:

```python
@router.get("/route-plans/live", response_model=list[LiveRoutePlan])
def get_live_route_plans(
    customer_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[LiveRoutePlan]:
    """Every route plan currently being tracked, with a simulated live
    position computed per request.

    Scoping is identical to `GET /route-plans` above: a `customer` caller
    only ever sees their own rows (any `?customer_id=` is ignored), a
    `support_agent` sees every customer's unless narrowed by
    `?customer_id=`.

    Filtered to `status=ACTIVE AND unavailable=False` -- a completed plan
    has finished and a failed one never had a route, so neither has
    anything live to show. Unlike `GET /route-plans` there is deliberately
    no date filter: "what is moving right now" is not a per-day question,
    and a stale active plan simply pins at 100% progress until someone
    marks it complete.

    Rows whose geometry carries no coordinates are skipped rather than
    plotted at a made-up position (see `_geometry_points`).
    """
    query = (
        db.query(RoutePlan, Driver)
        .outerjoin(Driver, RoutePlan.driver_id == Driver.driver_id)
        .filter(RoutePlan.status == RoutePlanStatus.ACTIVE, RoutePlan.unavailable.is_(False))
    )

    if current_user.role == "customer":
        query = query.filter(RoutePlan.customer_id == current_user.user_id)
    elif customer_id is not None:
        query = query.filter(RoutePlan.customer_id == customer_id)

    # One `now` for the whole response, so two rows in the same payload are
    # never computed against clocks a few milliseconds apart.
    now = datetime.now(timezone.utc)

    live: list[LiveRoutePlan] = []
    for row, driver in query.order_by(RoutePlan.created_at.desc()).all():
        if not _geometry_points(row.geometry):
            continue
        progress = compute_route_progress(row, now=now)
        live.append(
            LiveRoutePlan(
                **_row_to_list_item(row).model_dump(),
                driver_id=row.driver_id,
                driver_name=driver.full_name if driver is not None else None,
                current_lat=progress.current_lat,
                current_lon=progress.current_lon,
                progress_percent=progress.progress_percent,
                eta=progress.eta,
            )
        )
    return live
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_route_plans_live_api.py tests/test_route_plans_list_api.py tests/test_route_plan_complete_api.py -v`

Expected: all 13 tests in `test_route_plans_live_api.py` pass, and the pre-existing list/complete suites still pass (the new `/route-plans/live` path does not shadow `GET /route-plans` or `PATCH /route-plans/{id}/complete`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/route_plan.py backend/tests/test_route_plans_live_api.py
git commit -m "Add GET /route-plans/live with simulated positions and driver names"
```

---

## Task 5: Driver dropdown on the Routes plan form

**Files:**
- Modify: `frontend/src/pages/Routes.tsx` (module docstring lines 1-9, imports lines 10-14, state lines 91-101, `handlePlanRoute` lines 125-149, form markup lines 167-218)
- Test: `frontend/src/pages/Routes.test.tsx` (modify `mockRoutesFetch`, append tests)

**Interfaces:**
- Consumes: `RoutePlanRequest.driver_id` on `POST /route-plan` (Task 3); the existing `Driver` type in `frontend/src/types/telematics.ts` (`driver_id`, `customer_id`, `full_name`, `license_number`, `email`, `phone_number`, `created_at`) — unchanged, no new type needed; `apiGet`/`apiPost` from `frontend/src/lib/apiClient.ts`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

In `frontend/src/pages/Routes.test.tsx`, add the `Driver` type import below the existing `RoutePlanListItem` import:

```tsx
import type { Driver } from '../types/telematics'
```

Add this fixture immediately below the `completedRoute` constant:

```tsx
const drivers: Driver[] = [
  {
    driver_id: 11,
    customer_id: 100,
    full_name: 'Alice Driver',
    license_number: 'LIC-A',
    email: null,
    phone_number: null,
    created_at: '2026-09-01T00:00:00Z',
  },
  {
    driver_id: 22,
    customer_id: 200,
    full_name: 'Bob Driver',
    license_number: 'LIC-B',
    email: null,
    phone_number: null,
    created_at: '2026-09-01T00:00:00Z',
  },
]
```

Replace the whole `mockRoutesFetch` helper with this version (the `/drivers` branch must be tested before `/route-plans`, and the POST branch before both):

```tsx
function mockRoutesFetch(routes: RoutePlanListItem[] = [activeRoute, completedRoute]) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string, options?: RequestInit) => {
    if (options?.method === 'PATCH' && url.includes('/complete')) {
      return { ok: true, status: 200, json: async () => ({ route_plan_id: 1, status: 'completed', completed_at: '2026-09-18T10:00:00Z' }) }
    }
    if (options?.method === 'POST' && url.includes('/route-plan')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          route_plan_id: 3,
          distance_km: 5.0,
          duration_min: 10.0,
          geometry: { type: 'LineString', coordinates: [] },
          warnings: [],
          unavailable: false,
        }),
      }
    }
    if (url.includes('/drivers')) {
      return { ok: true, status: 200, json: async () => drivers }
    }
    if (url.includes('/route-plans')) {
      return { ok: true, status: 200, json: async () => routes }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}
```

Append these tests inside the existing `describe('RoutesPage', ...)` block:

```tsx
  it('lists the fleet drivers in the driver dropdown, defaulting to Unassigned', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Alice Driver' })).toBeInTheDocument()
    })
    const select = screen.getByLabelText(/^driver$/i) as HTMLSelectElement
    expect(select.value).toBe('')
    expect(screen.getByRole('option', { name: 'Unassigned' })).toBeInTheDocument()
  })

  it('sends the selected driver_id in the POST /route-plan body', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Alice Driver' })).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/origin/i), { target: { value: 'Sydney CBD' } })
    fireEvent.change(screen.getByLabelText(/destination/i), { target: { value: 'Bondi Beach' } })
    fireEvent.change(screen.getByLabelText(/^driver$/i), { target: { value: '11' } })
    fireEvent.click(screen.getByRole('button', { name: /plan route/i }))

    await waitFor(() => {
      const postCall = (fetch as unknown as Mock).mock.calls.find(
        ([url, options]) => options?.method === 'POST' && (url as string).includes('/route-plan')
      )
      expect(postCall).toBeTruthy()
      expect(JSON.parse(postCall![1].body as string)).toMatchObject({
        origin: 'Sydney CBD',
        destination: 'Bondi Beach',
        driver_id: 11,
      })
    })
  })

  it('omits driver_id from the POST body when no driver is selected', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Alice Driver' })).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/origin/i), { target: { value: 'Sydney CBD' } })
    fireEvent.change(screen.getByLabelText(/destination/i), { target: { value: 'Bondi Beach' } })
    fireEvent.click(screen.getByRole('button', { name: /plan route/i }))

    await waitFor(() => {
      const postCall = (fetch as unknown as Mock).mock.calls.find(
        ([url, options]) => options?.method === 'POST' && (url as string).includes('/route-plan')
      )
      expect(postCall).toBeTruthy()
      expect(JSON.parse(postCall![1].body as string)).not.toHaveProperty('driver_id')
    })
  })

  it('narrows the driver dropdown to the customer ID a support agent typed', async () => {
    loginAsSupportAgent()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Alice Driver' })).toBeInTheDocument()
    })
    expect(screen.getByRole('option', { name: 'Bob Driver' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(/^customer id$/i), { target: { value: '200' } })

    await waitFor(() => {
      expect(screen.queryByRole('option', { name: 'Alice Driver' })).not.toBeInTheDocument()
    })
    expect(screen.getByRole('option', { name: 'Bob Driver' })).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- Routes.test.tsx`

Expected: the 4 new tests fail — each times out in `waitFor` with `Unable to find an accessible element with the role "option" and name "Alice Driver"` (the page never fetches `/drivers` and renders no `<select>`). The 6 pre-existing tests still pass.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/pages/Routes.tsx`, extend the module docstring's last sentence so the block reads:

```tsx
/**
 * Route selector + daily route/risk tracking. Lets a customer (or, on
 * behalf of a customer, a support_agent) plan a route -- reusing the
 * route-planning feature's `POST /route-plan` and `RouteMap` -- and lists
 * everyone's routes planned today via `GET /route-plans`, with a "Mark
 * complete" action (`PATCH /route-plans/{id}/complete`). A support_agent
 * additionally sees every customer's routes and can filter by customer ID
 * -- see docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.
 *
 * The plan form also carries an optional driver assignment, used by the
 * Live Tracking page (`/tracking`) to label a moving route with a driver
 * name -- see
 * docs/superpowers/specs/2026-09-24-live-tracking-design.md.
 */
```

Add the `Driver` type import below the existing `RoutePlanListItem` import line:

```tsx
import type { Driver } from '../types/telematics'
```

Add this state below the existing `const [planCustomerId, setPlanCustomerId] = useState('')` line:

```tsx
  const [drivers, setDrivers] = useState<Driver[]>([])
  const [driversError, setDriversError] = useState<string | null>(null)
  const [driverId, setDriverId] = useState('')
```

Add this effect immediately below the existing `useEffect` that calls `loadRoutes`:

```tsx
  // `GET /drivers` is already customer-scoped server-side for a `customer`
  // caller, and returns every customer's drivers for a `support_agent` --
  // so the support-agent case is narrowed client-side to whichever
  // customer they typed into the plan form's Customer ID field, matching
  // how that field already scopes the saved plan itself.
  useEffect(() => {
    let cancelled = false

    async function loadDrivers() {
      try {
        const data = await apiGet<Driver[]>('/drivers')
        if (!cancelled) {
          setDrivers(data)
          setDriversError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setDrivers([])
          setDriversError(err instanceof Error ? err.message : 'Failed to load drivers.')
        }
      }
    }

    void loadDrivers()

    return () => {
      cancelled = true
    }
  }, [])
```

Add this derived list immediately above the existing `async function handlePlanRoute(...)`:

```tsx
  const selectableDrivers =
    isSupportAgent && planCustomerId
      ? drivers.filter((driver) => driver.customer_id === Number(planCustomerId))
      : drivers
```

In `handlePlanRoute`, replace the body-building lines so the `try` block starts:

```tsx
    try {
      const body: Record<string, unknown> = { origin, destination }
      if (isSupportAgent) {
        body.customer_id = Number(planCustomerId)
      }
      if (driverId) {
        body.driver_id = Number(driverId)
      }
      const result = await apiPost<RoutePlanResult>('/route-plan', body)
```

Add this markup inside the form, immediately after the support-agent Customer ID block (i.e. between the closing `)}` of `{isSupportAgent && (...)}` and the `<button type="submit" ...>`):

```tsx
        <div>
          <label htmlFor="route-driver" className="block text-sm text-gray-600 dark:text-gray-300">
            Driver
          </label>
          <select
            id="route-driver"
            value={driverId}
            onChange={(event) => setDriverId(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          >
            <option value="">Unassigned</option>
            {selectableDrivers.map((driver) => (
              <option key={driver.driver_id} value={driver.driver_id}>
                {driver.full_name}
              </option>
            ))}
          </select>
          {driversError && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">
              Failed to load drivers: {driversError}
            </p>
          )}
        </div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- Routes.test.tsx`

Expected: all 10 tests in `Routes.test.tsx` pass (6 pre-existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Routes.tsx frontend/src/pages/Routes.test.tsx
git commit -m "Add an optional driver picker to the plan-route form"
```

---

## Task 6: `LiveTrackingMap` component

**Files:**
- Create: `frontend/src/components/LiveTrackingMap.tsx`
- Modify: `frontend/src/types/routePlan.ts` (append after `RoutePlanListItem`, lines 42-58)
- Test: `frontend/src/components/LiveTrackingMap.test.tsx`

**Interfaces:**
- Consumes: the `GET /route-plans/live` JSON shape from Task 4 (`LiveRoutePlan`).
- Produces:
  - `LiveRoutePlan` TS interface in `frontend/src/types/routePlan.ts`: `RoutePlanListItem` plus `driver_id: number | null`, `driver_name: string | null`, `current_lat: number`, `current_lon: number`, `progress_percent: number`, `eta: string`.
  - `LiveTrackingMap({ routes }: { routes: LiveRoutePlan[] })` — named export plus default export, same dual-export convention as `RouteMap.tsx`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/LiveTrackingMap.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { LiveTrackingMap } from './LiveTrackingMap'
import type { LiveRoutePlan } from '../types/routePlan'

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="map-container">{children}</div>
  ),
  TileLayer: () => <div data-testid="tile-layer" />,
  Polyline: ({ positions }: { positions: [number, number][] }) => (
    <div data-testid="route-polyline" data-point-count={positions.length} />
  ),
  CircleMarker: ({
    center,
    pathOptions,
    children,
  }: {
    center: [number, number]
    pathOptions: { color: string }
    children: React.ReactNode
  }) => (
    <div data-testid="position-marker" data-lat={center[0]} data-lon={center[1]} data-color={pathOptions.color}>
      {children}
    </div>
  ),
  Popup: ({ children }: { children: React.ReactNode }) => <div data-testid="position-popup">{children}</div>,
}))

const ASSIGNED_ROUTE: LiveRoutePlan = {
  route_plan_id: 1,
  customer_id: 100,
  origin_label: 'Sydney CBD',
  destination_label: 'Parramatta',
  distance_km: 23.4,
  duration_min: 38.2,
  geometry: {
    type: 'LineString',
    coordinates: [
      [151.2093, -33.8688],
      [151.15, -33.84],
      [151.0011, -33.815],
    ],
  },
  warnings: [],
  unavailable: false,
  unavailable_reason: null,
  status: 'active',
  created_at: '2026-09-24T09:00:00Z',
  completed_at: null,
  driver_id: 11,
  driver_name: 'Alice Driver',
  current_lat: -33.84,
  current_lon: 151.15,
  progress_percent: 50,
  eta: '2026-09-24T09:38:00Z',
}

const UNASSIGNED_ROUTE: LiveRoutePlan = {
  ...ASSIGNED_ROUTE,
  route_plan_id: 2,
  destination_label: 'Bondi Beach',
  driver_id: null,
  driver_name: null,
  current_lat: -33.87,
  current_lon: 151.2,
  progress_percent: 10,
}

describe('LiveTrackingMap', () => {
  it('draws one polyline per tracked route', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE, UNASSIGNED_ROUTE]} />)

    expect(screen.getAllByTestId('route-polyline')).toHaveLength(2)
  })

  it('draws the polyline with one point per geometry coordinate', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE]} />)

    expect(screen.getByTestId('route-polyline')).toHaveAttribute('data-point-count', '3')
  })

  it('places a marker at each route current position in Leaflet lat/lon order', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE]} />)

    const marker = screen.getByTestId('position-marker')
    expect(marker).toHaveAttribute('data-lat', '-33.84')
    expect(marker).toHaveAttribute('data-lon', '151.15')
  })

  it('color-codes each tracked route differently', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE, UNASSIGNED_ROUTE]} />)

    const colors = screen.getAllByTestId('position-marker').map((m) => m.getAttribute('data-color'))
    expect(new Set(colors).size).toBe(2)
  })

  it('names the assigned driver in the marker popup', () => {
    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE]} />)

    expect(screen.getByText('Alice Driver')).toBeInTheDocument()
    expect(screen.getByText('Sydney CBD → Parramatta')).toBeInTheDocument()
  })

  it('labels an unassigned route as Unassigned in its popup', () => {
    render(<LiveTrackingMap routes={[UNASSIGNED_ROUTE]} />)

    expect(screen.getByText('Unassigned')).toBeInTheDocument()
  })

  it('skips a route whose geometry has no coordinates', () => {
    const empty: LiveRoutePlan = {
      ...ASSIGNED_ROUTE,
      route_plan_id: 3,
      geometry: { type: 'LineString', coordinates: [] },
    }

    render(<LiveTrackingMap routes={[ASSIGNED_ROUTE, empty]} />)

    expect(screen.getAllByTestId('route-polyline')).toHaveLength(1)
  })

  it('renders an empty state instead of a map when nothing is tracked', () => {
    render(<LiveTrackingMap routes={[]} />)

    expect(screen.queryByTestId('map-container')).not.toBeInTheDocument()
    expect(screen.getByTestId('live-tracking-map-empty')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- LiveTrackingMap.test.tsx`

Expected: the whole file fails to load — `Error: Failed to resolve import "./LiveTrackingMap" from "src/components/LiveTrackingMap.test.tsx"` — so all 8 tests are reported as failing/unrun.

- [ ] **Step 3: Write minimal implementation**

Append to `frontend/src/types/routePlan.ts`:

```ts
/** `GET /route-plans/live` row -- mirrors `LiveRoutePlan` in
 * `backend/app/api/route_plan.py`: everything `GET /route-plans` returns
 * plus the assigned driver and the simulated current position. The
 * position fields are recomputed by the backend on every request and are
 * never stored, so two polls a few seconds apart legitimately return
 * different `current_lat`/`current_lon` for the same `route_plan_id`.
 * `eta` is fixed at `created_at + duration_min` and does not move. */
export interface LiveRoutePlan extends RoutePlanListItem {
  driver_id: number | null
  /** Joined from the driver's `full_name`; null when unassigned. */
  driver_name: string | null
  current_lat: number
  current_lon: number
  /** 0-100 inclusive, already clamped server-side. */
  progress_percent: number
  eta: string
}
```

Create `frontend/src/components/LiveTrackingMap.tsx`:

```tsx
/**
 * Multi-route live tracking map: draws every actively-tracked route's line
 * plus a marker at its simulated current position, using the same
 * `react-leaflet` primitives and OpenStreetMap tiles `RouteMap.tsx` uses.
 *
 * Coordinate order, the one easy thing to get wrong here: values in
 * `geometry.coordinates` are GeoJSON order ([longitude, latitude]) and are
 * swapped below before reaching Leaflet, whose LatLngExpression is
 * [latitude, longitude] -- while `current_lat`/`current_lon` already
 * arrive from the backend as separate named fields and are used as-is.
 *
 * Positions are simulated server-side from each plan's created_at,
 * duration_min and geometry; no GPS hardware exists anywhere in this app
 * and nothing is stored per position. See
 * docs/superpowers/specs/2026-09-24-live-tracking-design.md.
 */
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { LiveRoutePlan } from '../types/routePlan'

interface LiveTrackingMapProps {
  routes: LiveRoutePlan[]
}

/** One color per tracked route, cycled by index, so two routes sharing a
 * stretch of road stay tellable apart. Deliberately not keyed off severity
 * or status -- every route drawn here is active and available. */
const ROUTE_COLORS = ['#4f46e5', '#0891b2', '#c2410c', '#15803d', '#a21caf']

function routeColor(index: number): string {
  return ROUTE_COLORS[index % ROUTE_COLORS.length]
}

export function LiveTrackingMap({ routes }: LiveTrackingMapProps) {
  const drawable = routes
    .map((route) => ({
      route,
      positions: (route.geometry?.coordinates ?? []).map(
        ([lon, lat]) => [lat, lon] as [number, number]
      ),
    }))
    .filter((entry) => entry.positions.length > 0)

  if (drawable.length === 0) {
    return (
      <div
        data-testid="live-tracking-map-empty"
        className="flex h-96 w-full items-center justify-center rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
      >
        <p className="text-sm text-gray-600 dark:text-gray-300">
          No routes are being tracked right now.
        </p>
      </div>
    )
  }

  const center: [number, number] = [drawable[0].route.current_lat, drawable[0].route.current_lon]

  return (
    <div
      data-testid="live-tracking-map"
      className="h-96 w-full overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
    >
      <MapContainer center={center} zoom={11} style={{ height: '100%', width: '100%' }} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {drawable.map(({ route, positions }, index) => (
          <Polyline
            key={`line-${route.route_plan_id}`}
            positions={positions}
            pathOptions={{ color: routeColor(index), weight: 4 }}
          />
        ))}
        {drawable.map(({ route }, index) => (
          <CircleMarker
            key={`position-${route.route_plan_id}`}
            center={[route.current_lat, route.current_lon]}
            radius={9}
            pathOptions={{
              color: routeColor(index),
              fillColor: routeColor(index),
              fillOpacity: 0.9,
            }}
          >
            <Popup>
              <strong>{route.driver_name ?? 'Unassigned'}</strong>
              <p>
                {route.origin_label} → {route.destination_label}
              </p>
              <p>{Math.round(route.progress_percent)}% complete</p>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}

export default LiveTrackingMap
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- LiveTrackingMap.test.tsx`

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LiveTrackingMap.tsx frontend/src/components/LiveTrackingMap.test.tsx frontend/src/types/routePlan.ts
git commit -m "Add a multi-route live tracking Leaflet map component"
```

---

## Task 7: `/tracking` page, polling, side list, and nav entry

**Files:**
- Create: `frontend/src/pages/LiveTracking.tsx`
- Modify: `frontend/src/App.tsx` (imports lines 1-11, routes lines 28-34)
- Modify: `frontend/src/components/Layout.tsx` (`NAV_LINKS`, lines 5-10)
- Test: `frontend/src/pages/LiveTracking.test.tsx`
- Test: `frontend/src/components/Layout.test.tsx`

**Interfaces:**
- Consumes: `GET /route-plans/live?customer_id=<id>` (Task 4); `LiveRoutePlan` type and `LiveTrackingMap({ routes }: { routes: LiveRoutePlan[] })` (Task 6); `useAuth()` from `frontend/src/context/AuthProvider.tsx`; `apiGet` from `frontend/src/lib/apiClient.ts`.
- Produces: default-exported `LiveTracking()` page component at route `/tracking`, plus the `{ to: '/tracking', label: 'Live Tracking' }` nav entry.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/LiveTracking.test.tsx`:

```tsx
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { makeFakeJwt } from '../test-support/jwt'
import LiveTracking from './LiveTracking'
import type { LiveRoutePlan } from '../types/routePlan'

vi.mock('../components/LiveTrackingMap', () => ({
  LiveTrackingMap: ({ routes }: { routes: LiveRoutePlan[] }) => (
    <div data-testid="mock-live-tracking-map" data-route-count={routes.length} />
  ),
}))

function renderPage() {
  return render(
    <AuthProvider>
      <LiveTracking />
    </AuthProvider>
  )
}

function loginAsCustomer() {
  const token = makeFakeJwt({
    sub: '100',
    role: 'customer',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

function loginAsSupportAgent() {
  const token = makeFakeJwt({
    sub: '7',
    role: 'support_agent',
    access_level: 'admin',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

const assignedRoute: LiveRoutePlan = {
  route_plan_id: 1,
  customer_id: 100,
  origin_label: 'Sydney CBD',
  destination_label: 'Parramatta',
  distance_km: 23.4,
  duration_min: 38.2,
  geometry: { type: 'LineString', coordinates: [[151.2093, -33.8688], [151.0011, -33.815]] },
  warnings: [],
  unavailable: false,
  unavailable_reason: null,
  status: 'active',
  created_at: '2026-09-24T09:00:00Z',
  completed_at: null,
  driver_id: 11,
  driver_name: 'Alice Driver',
  current_lat: -33.84,
  current_lon: 151.15,
  progress_percent: 50,
  eta: '2026-09-24T09:38:00Z',
}

const unassignedRoute: LiveRoutePlan = {
  ...assignedRoute,
  route_plan_id: 2,
  destination_label: 'Bondi Beach',
  driver_id: null,
  driver_name: null,
  progress_percent: 10,
}

function mockLiveFetch(routes: LiveRoutePlan[] = [assignedRoute, unassignedRoute]) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string) => {
    if (url.includes('/route-plans/live')) {
      return { ok: true, status: 200, json: async () => routes }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('LiveTracking', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists one row per tracked route fetched from GET /route-plans/live', async () => {
    loginAsCustomer()
    mockLiveFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('live-route-1')).toHaveTextContent('Sydney CBD → Parramatta')
    })
    expect(screen.getByTestId('live-route-1')).toHaveTextContent('Alice Driver')
    expect(screen.getByTestId('live-route-2')).toHaveTextContent('Unassigned')
    expect(screen.getByText('Tracking 2 route(s)')).toBeInTheDocument()
  })

  it('shows each route progress as a labelled progress bar', async () => {
    loginAsCustomer()
    mockLiveFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('live-progress-1')).toBeInTheDocument()
    })
    expect(screen.getByTestId('live-progress-1')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByTestId('live-progress-2')).toHaveAttribute('aria-valuenow', '10')
  })

  it('passes the tracked routes to the map', async () => {
    loginAsCustomer()
    mockLiveFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('mock-live-tracking-map')).toHaveAttribute('data-route-count', '2')
    })
  })

  it('shows an empty state when nothing is being tracked', async () => {
    loginAsCustomer()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No routes are being tracked right now.')).toBeInTheDocument()
    })
  })

  it('shows a customer-ID filter only for a support_agent caller', async () => {
    loginAsSupportAgent()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText(/filter by customer id/i)).toBeInTheDocument()
    })
  })

  it('does not show the customer-ID filter for a customer caller', async () => {
    loginAsCustomer()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No routes are being tracked right now.')).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/filter by customer id/i)).not.toBeInTheDocument()
  })

  it('refetches with the customer_id a support agent typed', async () => {
    loginAsSupportAgent()
    mockLiveFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText(/filter by customer id/i)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/filter by customer id/i), { target: { value: '200' } })

    await waitFor(() => {
      const urls = (fetch as unknown as Mock).mock.calls.map(([url]) => url as string)
      expect(urls.some((url) => url.includes('/route-plans/live?customer_id=200'))).toBe(true)
    })
  })

  it('polls the live endpoint again after the poll interval elapses', async () => {
    loginAsCustomer()
    mockLiveFetch()
    // `shouldAdvanceTime` keeps the real clock running underneath the fake
    // timers, so Testing Library's own `waitFor` polling still works while
    // the page's setInterval is driven forward explicitly below.
    vi.useFakeTimers({ shouldAdvanceTime: true })

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('live-route-1')).toBeInTheDocument()
    })
    const callsAfterFirstLoad = (fetch as unknown as Mock).mock.calls.length

    await vi.advanceTimersByTimeAsync(5000)

    expect((fetch as unknown as Mock).mock.calls.length).toBeGreaterThan(callsAfterFirstLoad)
  })
})
```

Create `frontend/src/components/Layout.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../context/AuthProvider'
import { Layout } from './Layout'

vi.mock('./ChatWidget', () => ({
  ChatWidget: () => <div data-testid="mock-chat-widget" />,
}))

function renderLayout() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/overview']}>
        <Layout />
      </MemoryRouter>
    </AuthProvider>
  )
}

describe('Layout', () => {
  it('links to the Live Tracking page from the nav bar', () => {
    renderLayout()

    expect(screen.getByRole('link', { name: 'Live Tracking' })).toHaveAttribute('href', '/tracking')
  })

  it('keeps the pre-existing nav entries', () => {
    renderLayout()

    expect(screen.getByRole('link', { name: 'Overview' })).toHaveAttribute('href', '/overview')
    expect(screen.getByRole('link', { name: 'Routes' })).toHaveAttribute('href', '/routes')
    expect(screen.getByRole('link', { name: 'Drivers' })).toHaveAttribute('href', '/drivers')
    expect(screen.getByRole('link', { name: 'Alerts' })).toHaveAttribute('href', '/alerts')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- LiveTracking.test.tsx Layout.test.tsx`

Expected: `LiveTracking.test.tsx` fails to load with `Error: Failed to resolve import "./LiveTracking" from "src/pages/LiveTracking.test.tsx"` (all 8 tests unrun), and `Layout.test.tsx`'s first test fails with `Unable to find an accessible element with the role "link" and name "Live Tracking"` while its second test passes.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/pages/LiveTracking.tsx`:

```tsx
/**
 * Live Tracking page (`/tracking`): polls `GET /route-plans/live` every
 * POLL_INTERVAL_MS and shows every actively-tracked route on one map
 * (`LiveTrackingMap`) beside a list giving the driver (or "Unassigned"),
 * origin -> destination, a progress bar, and the ETA.
 *
 * Positions are simulated server-side from each plan's created_at,
 * duration_min and geometry -- there is no GPS or device hardware anywhere
 * in this app, and nothing is stored per position. See
 * docs/superpowers/specs/2026-09-24-live-tracking-design.md.
 *
 * Polling with a plain `setInterval` (cleared on unmount) rather than a
 * WebSocket: every other page in this app fetches the same way, and push
 * delivery is explicitly out of scope for the approved design.
 *
 * A support_agent gets the same customer-ID filter input the Routes page
 * already has; a customer sees only their own routes (enforced by the
 * backend, which ignores any customer_id a customer sends).
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { LiveTrackingMap } from '../components/LiveTrackingMap'
import type { LiveRoutePlan } from '../types/routePlan'

const POLL_INTERVAL_MS = 5000

/** Local clock time for an ISO timestamp, em dash if it's unparseable --
 * same formatting convention as Routes.tsx's `formatTime`. */
function formatEta(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export default function LiveTracking() {
  const { user } = useAuth()
  const isSupportAgent = user?.role === 'support_agent'

  const [routes, setRoutes] = useState<LiveRoutePlan[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [filterCustomerId, setFilterCustomerId] = useState('')

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const query =
          isSupportAgent && filterCustomerId
            ? `?customer_id=${encodeURIComponent(filterCustomerId)}`
            : ''
        const data = await apiGet<LiveRoutePlan[]>(`/route-plans/live${query}`)
        if (!cancelled) {
          setRoutes(data)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load live routes.')
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    void load()
    const timer = setInterval(() => {
      void load()
    }, POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [isSupportAgent, filterCustomerId])

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Live Tracking</h1>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Simulated positions, refreshed every {POLL_INTERVAL_MS / 1000} seconds.
      </p>

      {isSupportAgent && (
        <div className="mt-4">
          <label
            htmlFor="live-filter-customer-id"
            className="block text-sm text-gray-600 dark:text-gray-300"
          >
            Filter by customer ID
          </label>
          <input
            id="live-filter-customer-id"
            value={filterCustomerId}
            onChange={(event) => setFilterCustomerId(event.target.value)}
            placeholder="All customers"
            className="mt-1 w-40 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load live routes: {error}
        </p>
      )}

      {isLoading ? (
        <p className="mt-4 text-gray-600 dark:text-gray-300">Loading live routes…</p>
      ) : (
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <LiveTrackingMap routes={routes} />
          </div>

          <div>
            <h2 className="text-sm font-semibold uppercase text-gray-500 dark:text-gray-400">
              Tracking {routes.length} route(s)
            </h2>
            {routes.length === 0 ? (
              <p className="mt-2 text-gray-600 dark:text-gray-300">
                No routes are being tracked right now.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
                {routes.map((route) => (
                  <li
                    key={route.route_plan_id}
                    data-testid={`live-route-${route.route_plan_id}`}
                    className="px-4 py-3"
                  >
                    <span className="text-sm font-medium text-gray-900 dark:text-white">
                      {route.driver_name ?? 'Unassigned'}
                    </span>
                    <p className="text-xs text-gray-600 dark:text-gray-300">
                      {route.origin_label} → {route.destination_label}
                    </p>
                    <div
                      data-testid={`live-progress-${route.route_plan_id}`}
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(route.progress_percent)}
                      aria-label={`${route.origin_label} to ${route.destination_label} progress`}
                      className="mt-2 h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700"
                    >
                      <div
                        className="h-full rounded-full bg-indigo-600"
                        style={{ width: `${Math.round(route.progress_percent)}%` }}
                      />
                    </div>
                    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      {Math.round(route.progress_percent)}% · ETA {formatEta(route.eta)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
```

In `frontend/src/App.tsx`, add the import immediately after the `RoutesPage` import:

```tsx
import LiveTracking from './pages/LiveTracking'
```

and add the route immediately after the `/routes` route:

```tsx
              <Route path="/tracking" element={<LiveTracking />} />
```

In `frontend/src/components/Layout.tsx`, replace `NAV_LINKS` with:

```tsx
const NAV_LINKS = [
  { to: '/overview', label: 'Overview' },
  { to: '/routes', label: 'Routes' },
  { to: '/tracking', label: 'Live Tracking' },
  { to: '/drivers', label: 'Drivers' },
  { to: '/alerts', label: 'Alerts' },
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test`

Expected: the whole frontend suite passes, including the 8 new `LiveTracking.test.tsx` tests and the 2 new `Layout.test.tsx` tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/LiveTracking.tsx frontend/src/pages/LiveTracking.test.tsx frontend/src/components/Layout.tsx frontend/src/components/Layout.test.tsx frontend/src/App.tsx
git commit -m "Add the Live Tracking page, polling, and nav entry"
```

---

## Task 8: Documentation

**Files:**
- Modify: `docs/DATA_MODEL.md` (the `### RoutePlan` field table, lines 226-241)
- Modify: `docs/API_REFERENCE.md` (`### POST /route-plan` request section, lines 218-231; new section after `### GET /route-plans`, which ends at line 308)
- Modify: `docs/ROUTE_PLANNING.md` (new section between `### Chat: "today's routes" intent` and `## Frontend` at line 418; `## Testing` section at lines 514-526)

**Interfaces:**
- Consumes: every signature produced by Tasks 1-7 (`RoutePlan.driver_id`, `save_route_plan(..., driver_id=None)`, `interpolate_position`, `compute_route_progress`, `GET /route-plans/live` → `LiveRoutePlan`, `LiveTrackingMap`, `/tracking`).
- Produces: documentation only; nothing consumes it.

- [ ] **Step 1: Write the failing test**

No test. Documentation prose has no executable assertion in this repo, and inventing one (e.g. grepping docs for a string) would test the grep, not the docs. Verification is Step 2's read-back check instead.

- [ ] **Step 2: Run test to verify it fails**

Run (from the repository root): `grep -n "driver_id" docs/DATA_MODEL.md docs/API_REFERENCE.md docs/ROUTE_PLANNING.md`

Expected: no output at all (exit status 1) — none of the three docs mentions `driver_id` or live tracking yet.

- [ ] **Step 3: Write minimal implementation**

In `docs/DATA_MODEL.md`, insert this row into the `### RoutePlan` field table immediately after the `| \`created_by_id\` | int | ... |` row:

```markdown
| `driver_id` | int, FK → Driver, nullable | optional driver assignment used by live tracking (`GET /route-plans/live`); `null` means "Unassigned". Set once, at `POST /route-plan` time — there is deliberately no reassignment path, matching how origin/destination are also immutable after creation. Validated against the plan's own `customer_id` fleet on write |
```

In `docs/API_REFERENCE.md`, replace the `### POST /route-plan` **Request** block and its bullet list with:

```markdown
**Request** (`RoutePlanRequest`)
```json
{ "origin": "Sydney CBD", "destination": "Parramatta", "waypoints": null, "customer_id": null, "driver_id": null }
```
- `origin`/`destination`/each `waypoints` entry accept either a place-name
  string (geocoded server-side via OpenRouteService) or
  `{"lat": ..., "lon": ...}`.
- `customer_id` — same convention as `POST /reports/*`'s `ReportRequest`:
  ignored for a `customer`-role caller (the saved plan always uses
  `current_user.user_id`); **required** (`400` if omitted) for a
  `support_agent`, since they have no fleet of their own to default to.
- `driver_id` — optional. When given, it must name a `Driver` in the
  **resolved** customer's fleet (so a `support_agent` planning on a
  customer's behalf passes one of *that* customer's drivers), otherwise
  `400`. A nonexistent id and another customer's id are deliberately
  indistinguishable in the response. Omitted/`null` saves the plan
  unassigned, which still tracks — see
  [`GET /route-plans/live`](#get-route-planslive).
```

In `docs/API_REFERENCE.md`, insert this new section immediately after the `### GET /route-plans` section (i.e. directly before `### PATCH /route-plans/{route_plan_id}/complete`):

```markdown
### `GET /route-plans/live`

Requires `require_role("customer", "support_agent")`. Every route plan
currently being tracked, with a **simulated** live position computed per
request. There is no GPS or device hardware anywhere in this app — the
position is a pure function of `(created_at, duration_min, geometry, now)`
and is **never stored**, so two polls seconds apart legitimately return
different coordinates for the same `route_plan_id`. See
[ROUTE_PLANNING.md](ROUTE_PLANNING.md#live-tracking).

**Query params**

| Param | Meaning |
|---|---|
| `customer_id` | `support_agent` only — narrows to one customer; ignored for a `customer` caller |

Rows are filtered to `status="active"` **and** `unavailable=false` (a
completed plan has finished, a failed one never had a route), and a row
whose `geometry` carries no coordinates is skipped rather than plotted at
a made-up position. Unlike `GET /route-plans` there is no `date` filter:
"what is moving right now" is not a per-day question, and a stale active
plan simply pins at 100% until someone completes it.

**Response** (`list[LiveRoutePlan]`) `200` — every `GET /route-plans`
field, plus:
```json
[
  {
    "route_plan_id": 42,
    "customer_id": 1,
    "origin_label": "Sydney CBD",
    "destination_label": "Parramatta",
    "distance_km": 24.14,
    "duration_min": 27.6,
    "geometry": { "type": "LineString", "coordinates": [[151.21, -33.87], ...] },
    "warnings": [ /* same shape as POST /route-plan's warnings */ ],
    "unavailable": false,
    "unavailable_reason": null,
    "status": "active",
    "created_at": "2026-09-24T09:15:00+00:00",
    "completed_at": null,
    "driver_id": 7,
    "driver_name": "Alice Driver",
    "current_lat": -33.842,
    "current_lon": 151.021,
    "progress_percent": 50.0,
    "eta": "2026-09-24T09:42:36+00:00"
  }
]
```

| Field | Meaning |
|---|---|
| `driver_id` / `driver_name` | The assigned driver, joined from `drivers.full_name`; both `null` when unassigned |
| `current_lat` / `current_lon` | Simulated position, interpolated along `geometry` by distance |
| `progress_percent` | `clamp((now - created_at) / duration_min, 0, 1) * 100` |
| `eta` | `created_at + duration_min` — **fixed**, it does not slide forward as time passes |

Ordered newest-first (`created_at` descending). Scoping is identical to
`GET /route-plans` above.
```

In `docs/ROUTE_PLANNING.md`, insert this new section immediately before the `## Frontend` heading:

```markdown
## Live tracking

`GET /route-plans/live` answers "where is everything right now?" — for a
customer watching their own plans, and for a manager watching every
driver who is out.

**There is no GPS or device hardware anywhere in this codebase** (all
telematics data is synthetic/seeded). "Live" here means the backend
computes a position along the route's already-known geometry, proportional
to elapsed time since the plan was created. That is the intended design
for this POC, not a placeholder for a later real integration.

### The maths (`app/ai/route_tracking.py`)

Two pure functions — no FastAPI imports, no session, no network, the same
shape as `sample_route_points` above, and using the same
`haversine_distance_km` (`app/geo.py`) so a tracked position and a warning
distance always agree about how far along a route a point is:

- `interpolate_position(geometry, fraction) -> (lat, lon)` walks the
  GeoJSON `coordinates` list accumulating segment distances, then linearly
  interpolates inside the segment the target distance lands in. It is
  distance-weighted, not index-weighted, so the simulated vehicle moves at
  a constant speed rather than at a rate set by how densely ORS happened
  to sample that stretch of road. A geometry with no coordinates raises
  `ValueError` — the endpoint filters those rows out rather than plotting
  a vehicle at `(0, 0)`.
- `compute_route_progress(route_plan, *, now) -> RouteProgress` gives
  `(current_lat, current_lon, progress_percent, eta)`, with
  `progress_percent = clamp((now - created_at) / duration_min, 0, 1) * 100`
  and `eta = created_at + duration_min` — fixed, matching how the Routes
  page's existing "Estimated arrival" already works. A missing or
  non-positive `duration_min` reads as 100% complete (nothing left to
  simulate, and no division by zero). `created_at` is normalized to UTC
  first, because SQLite returns tz-naive values for a
  `DateTime(timezone=True)` column.

**Nothing is stored.** A position is recomputed on every read, which means
no background job or scheduler (this app has none), nothing to keep in
sync, and correct behavior for free across a backend restart. There is
also no historical trail or replay — only the current position exists.

### Driver assignment

`route_plans.driver_id` (nullable FK → `drivers.driver_id`) is set once,
at `POST /route-plan` time, and validated by `_resolve_driver_id` in
`app/api/route_plan.py` against the **resolved** customer's own fleet —
`400`, with a nonexistent id and another customer's id deliberately
indistinguishable. `save_route_plan(..., driver_id=None)` persists it; the
chat route-plan intent passes `driver_id=None` explicitly, since chat has
no driver-selection surface. There is no reassignment endpoint, matching
how origin/destination are also immutable after creation.

### Frontend (`/tracking`)

`frontend/src/pages/LiveTracking.tsx` polls `GET /route-plans/live` every
5 seconds with a plain `setInterval` cleared on unmount — polling, not a
WebSocket, because every other page in this app fetches the same way and
push delivery is explicitly out of scope. It renders
`frontend/src/components/LiveTrackingMap.tsx` (every tracked route's
polyline plus a color-coded marker at its current position, on the same
`react-leaflet` primitives `RouteMap` uses) beside a list giving the
driver name or "Unassigned", origin → destination, a progress bar, and the
ETA. A `support_agent` gets the same customer-ID filter input the Routes
page already has; a customer sees only their own. The plan form on the
Routes page gains an optional "Driver" `<select>` populated from
`GET /drivers`, narrowed client-side to the typed customer ID for a
`support_agent`.
```

In `docs/ROUTE_PLANNING.md`, replace the first paragraph of the `## Testing` section with:

```markdown
Every external call (`httpx.get`/`httpx.post` for ORS and Open-Meteo, and
`chat_completion` for the LLM) is mocked in tests — no live network access
required. Backend tests: `test_geo.py`, `test_openrouteservice.py`,
`test_open_meteo.py`, `test_datasource.py` (risk-zone lookup),
`test_route_planning.py` + `test_route_planning_risk_zones.py` +
`test_build_route_plan.py` (orchestration), `test_route_tracking.py`
(deterministic live-position maths against a fixed `now`),
`test_route_plan_api.py` (including `driver_id` validation),
`test_route_plans_live_api.py` (live scoping, filtering and computed
fields), `test_chat_api.py` (intent routing). Frontend:
`RouteMap.test.tsx` and `LiveTrackingMap.test.tsx` (both mock
`react-leaflet` entirely — jsdom can't render real Leaflet DOM),
`LiveTracking.test.tsx` (mocked polling fetch), `ChatWidget.test.tsx`
(mocks `RouteMap`, including a regression test for the sticky-panel bug
above).
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repository root): `grep -c "driver_id" docs/DATA_MODEL.md docs/API_REFERENCE.md docs/ROUTE_PLANNING.md`

Expected: a non-zero count for each of the three files, and each file's `## Live tracking` / `### GET /route-plans/live` / `driver_id` additions read back as written above. Then run the full suites one last time to confirm nothing regressed: `cd backend && pytest -v` (all tests pass) and `cd frontend && npm run test` (all tests pass).

- [ ] **Step 5: Commit**

```bash
git add docs/DATA_MODEL.md docs/API_REFERENCE.md docs/ROUTE_PLANNING.md
git commit -m "Document simulated live route tracking"
```
