"""Tests for app.ai.route_planning."""
from __future__ import annotations

from unittest.mock import patch

from app.ai.route_planning import DEFAULT_SAMPLE_POINT_COUNT, SamplePoint, evaluate_weather_warnings, sample_route_points
from app.integrations.open_meteo import ForecastResult, WeatherServiceRequestError

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


def _point(distance=0.0):
    return SamplePoint(latitude=-33.87, longitude=151.21, distance_from_origin_km=distance)


def test_evaluate_weather_warnings_flags_heavy_rain():
    with patch("app.ai.route_planning.get_forecast") as mock_forecast:
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=72.0, wind_speed_kmh=10.0, visibility_m=20000.0
        )

        warnings = evaluate_weather_warnings([_point()])

        assert len(warnings) == 1
        assert warnings[0].type == "weather"
        assert "rain" in warnings[0].description.lower()


def test_evaluate_weather_warnings_flags_strong_wind():
    with patch("app.ai.route_planning.get_forecast") as mock_forecast:
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=10.0, wind_speed_kmh=55.0, visibility_m=20000.0
        )

        warnings = evaluate_weather_warnings([_point()])

        assert len(warnings) == 1
        assert "wind" in warnings[0].description.lower()


def test_evaluate_weather_warnings_flags_low_visibility():
    with patch("app.ai.route_planning.get_forecast") as mock_forecast:
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=10.0, wind_speed_kmh=10.0, visibility_m=500.0
        )

        warnings = evaluate_weather_warnings([_point()])

        assert len(warnings) == 1
        assert "visibility" in warnings[0].description.lower()


def test_evaluate_weather_warnings_no_flag_below_all_thresholds():
    with patch("app.ai.route_planning.get_forecast") as mock_forecast:
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=10.0, wind_speed_kmh=10.0, visibility_m=20000.0
        )

        warnings = evaluate_weather_warnings([_point()])

        assert warnings == []


def test_evaluate_weather_warnings_swallows_per_point_failures():
    with patch("app.ai.route_planning.get_forecast") as mock_forecast:
        mock_forecast.side_effect = WeatherServiceRequestError("network down")

        warnings = evaluate_weather_warnings([_point(), _point(distance=5.0)])

        assert warnings == []
