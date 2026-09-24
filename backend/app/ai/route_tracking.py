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
