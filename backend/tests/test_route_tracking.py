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
