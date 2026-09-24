# Live Route Tracking (Simulated)

## Problem

The route-selector feature (`RoutePlan`) computes a route once and shows it
statically — a customer can't watch their vehicle's progress, and a manager
has no way to see which drivers are actively out on a route right now. This
adds simulated live position tracking for every `active` `RoutePlan`, and a
dedicated page for both audiences to watch it.

**No real GPS/device hardware exists anywhere in this codebase** (all
telematics data is synthetic/seeded) — "real-time" here means the backend
computes a simulated position along the route's already-known geometry,
proportional to elapsed time since the plan was created. This is not a
placeholder to be swapped for a real integration later within this spec's
scope; it is the intended design for this POC.

## Data model

Add one nullable column to the existing `route_plans` table:

| Field | Type | Notes |
|---|---|---|
| `driver_id` | int, FK -> `drivers.driver_id`, nullable | Optional -- a route plan with no assigned driver still tracks, just shown as "Unassigned". New Alembic migration, chains onto the current head. |

No other schema changes. Position is **never stored** — it's a pure
function of `(created_at, duration_min, geometry, now)`, recomputed on every
read. This means: no background job/scheduler (this app has none), nothing
to keep in sync, and it survives a backend restart for free.

## Backend changes

### `POST /route-plan`: accept `driver_id`

`RoutePlanRequest` gains `driver_id: int | None = None`. If given, the
handler validates it resolves to a `Driver` row whose `customer_id` matches
the resolved `customer_id` for this request (same "belongs to this
customer or 404" pattern already used for cross-tenant checks elsewhere) —
400 if it doesn't. `save_route_plan()` gains a `driver_id` parameter and
persists it on the `RoutePlan` row. The chat route-plan intent
(`app/api/chat.py`) is unaffected — it never sets a driver, so its
`save_route_plan()` calls simply don't pass one (defaults to `None`).

### New module: `app/ai/route_tracking.py`

Pure functions, no FastAPI/DB imports (mirrors `route_planning.py`'s own
shape):

```python
def interpolate_position(geometry: dict, fraction: float) -> tuple[float, float]:
    """(lat, lon) at `fraction` (0.0-1.0, already clamped by the caller) of
    the way along a GeoJSON LineString's total distance, walking the
    coordinate list and using haversine_distance_km (app/geo.py) between
    consecutive points -- the same distance approach
    route_planning.sample_route_points already uses, so the two stay
    consistent. Returns the first coordinate if geometry has 0-1 points."""

def compute_route_progress(
    route_plan: RoutePlan, *, now: datetime
) -> RouteProgress:
    """RouteProgress(current_lat, current_lon, progress_percent, eta).
    progress_percent = clamp((now - created_at) / duration_min, 0.0, 1.0) * 100,
    using minutes for both sides. eta = created_at + duration_min (fixed --
    doesn't move as time passes, matching how Routes.tsx's existing
    "Estimated arrival" already works). current_lat/lon from
    interpolate_position(geometry, progress_percent / 100)."""
```

### New endpoint: `GET /route-plans/live`

Same file as the other route-plan endpoints (`app/api/route_plan.py`).
Same RBAC/scoping as `GET /route-plans` (`customer` -> own only;
`support_agent` -> all, or narrowed by `?customer_id=`), but filtered to
`status == ACTIVE AND unavailable == False` only (a completed or failed
plan has nothing live to show). Each row is `RoutePlanListItem`'s existing
shape plus:

```python
class LiveRoutePlan(RoutePlanListItem):
    driver_id: int | None
    driver_name: str | None  # joined from Driver.full_name; None if unassigned
    current_lat: float
    current_lon: float
    progress_percent: float
    eta: datetime
```

## Frontend changes

### Routes page: optional driver picker

The plan-route form gains a "Driver" `<select>`, populated from
`GET /drivers` (scoped to the resolved customer the same way the existing
customer-ID field already works for a `support_agent`), with an
"Unassigned" default option. Sends `driver_id` in the `POST /route-plan`
body when set.

### New page: Live Tracking (`/tracking`)

New nav entry in `Layout.tsx`. Polls `GET /route-plans/live` every 5
seconds (`setInterval`, cleared on unmount). Renders:

- A map (new `LiveTrackingMap.tsx` component, built on the same
  `react-leaflet` primitives `RouteMap.tsx` already uses) drawing every
  tracked route's line plus a marker at its current position.
- A side list: one row per tracked route showing driver name (or
  "Unassigned"), origin -> destination, a progress bar, and ETA.
- A `support_agent` gets the same customer-ID filter input pattern already
  on the Routes page; a `customer` sees only their own.

No new frontend routing library/state-management dependency — reuses
`react-router-dom` (already in use) and plain `useEffect`/`setInterval`
polling, the same pattern every other page already uses for its initial
fetch.

## Testing

- Backend: deterministic unit tests for `interpolate_position`/
  `compute_route_progress` against a fixed `now` (0%, 50%, 100%, and
  past-100%-stays-at-100% cases). API tests for `GET /route-plans/live`
  scoping (customer-own-only, support_agent-all, `?customer_id=` filter,
  excludes completed/unavailable rows) and `driver_id` validation on
  `POST /route-plan` (accepts own customer's driver, 400s on another
  customer's driver or a nonexistent id).
- Frontend: a new test file for the Live Tracking page (mocked polling
  fetch, asserts the list/map render, asserts the customer-ID filter
  visibility rule matches Routes.tsx's existing pattern), plus a small
  addition to `Routes.test.tsx` for the new driver dropdown.

## Out of scope (explicitly, to keep this bounded)

- No real GPS/device integration — simulated movement is the designed
  behavior for this POC, not a stopgap.
- No WebSocket/push delivery — polling only, per the approved design.
- No historical position trail/replay — only the current live position is
  shown, nothing is stored over time.
- No driver reassignment after a route is created (`driver_id` is set once,
  at `POST /route-plan` time, matching how origin/destination are also
  immutable after creation per the existing route-selector design).
