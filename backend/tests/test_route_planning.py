"""Tests for app.ai.route_planning."""
from __future__ import annotations

from app.ai.route_planning import DEFAULT_SAMPLE_POINT_COUNT, sample_route_points

_STRAIGHT_LINE_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[151.2093, -33.8688 - i * 0.01] for i in range(20)],
}


def test_sample_route_points_returns_target_count_for_long_route():
    points = sample_route_points(_STRAIGHT_LINE_GEOMETRY, target_count=8)

    assert len(points) == 8


def test_sample_route_points_first_point_has_zero_distance_from_origin():
    points = sample_route_points(_STRAIGHT_LINE_GEOMETRY, target_count=8)

    assert points[0].distance_from_origin_km == 0.0


def test_sample_route_points_distances_increase_monotonically():
    points = sample_route_points(_STRAIGHT_LINE_GEOMETRY, target_count=8)

    distances = [p.distance_from_origin_km for p in points]
    assert distances == sorted(distances)
    assert distances[-1] > 0


def test_sample_route_points_returns_all_points_for_short_route():
    short_geometry = {"type": "LineString", "coordinates": [[151.2, -33.87], [151.21, -33.87]]}

    points = sample_route_points(short_geometry, target_count=8)

    assert len(points) == 2


def test_sample_route_points_returns_empty_list_for_missing_coordinates():
    assert sample_route_points({"type": "LineString", "coordinates": []}) == []


def test_default_sample_point_count_is_eight():
    assert DEFAULT_SAMPLE_POINT_COUNT == 8
