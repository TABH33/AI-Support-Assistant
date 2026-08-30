"""Tests for app.integrations.open_meteo."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from app.integrations.open_meteo import (
    ForecastResult,
    WeatherServiceRequestError,
    WeatherServiceResponseError,
    get_forecast,
)

_FAKE_REQUEST = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")


def _mock_response(status_code=200, json_body=None, text=None):
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=_FAKE_REQUEST)
    return httpx.Response(status_code, text=text or "", request=_FAKE_REQUEST)


def _hour_stamp(offset_hours: int) -> str:
    """An Open-Meteo-shaped naive ISO timestamp offset from the current UTC
    hour (Open-Meteo defaults to GMT+0 when no timezone param is sent)."""
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    return (hour + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M")


def _day_of_hours(values_by_offset):
    """Build an `hourly` block spanning yesterday-ish to tomorrow-ish, where
    `values_by_offset` maps an hour offset (relative to now) to a
    (precipitation, wind, visibility) triple."""
    offsets = sorted(values_by_offset)
    return {
        "time": [_hour_stamp(o) for o in offsets],
        "precipitation_probability": [values_by_offset[o][0] for o in offsets],
        "wind_speed_10m": [values_by_offset[o][1] for o in offsets],
        "visibility": [values_by_offset[o][2] for o in offsets],
    }


def test_get_forecast_returns_parsed_result():
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            json_body={"hourly": _day_of_hours({0: (72, 15.2, 24140.0), 1: (80, 18.0, 20000.0)})}
        )

        result = get_forecast(-33.87, 151.21)

        assert result == ForecastResult(
            precipitation_probability=72.0, wind_speed_kmh=15.2, visibility_m=24140.0
        )
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["latitude"] == -33.87
        assert kwargs["params"]["longitude"] == 151.21


def test_get_forecast_selects_the_current_hour_not_index_zero():
    """Final-review Fix 7 regression: Open-Meteo's `hourly` array starts at
    00:00 of the first forecast day, not at "now". Reading index 0 returned
    weather up to ~24h stale. The entry matching the current UTC hour must
    win, even though it is not first in the array."""
    hourly = _day_of_hours(
        {
            -3: (5, 1.0, 30000.0),
            -2: (6, 2.0, 30000.0),
            -1: (7, 3.0, 30000.0),
            0: (91, 44.0, 800.0),  # <- the current hour
            1: (10, 4.0, 30000.0),
        }
    )
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(json_body={"hourly": hourly})

        result = get_forecast(-33.87, 151.21)

    assert result == ForecastResult(
        precipitation_probability=91.0, wind_speed_kmh=44.0, visibility_m=800.0
    )


def test_get_forecast_requests_enough_days_to_always_have_an_upcoming_hour():
    """`forecast_days=1` only covers 00:00-23:00 of the current UTC day, so a
    late-evening request had no upcoming hour to select at all."""
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            json_body={"hourly": _day_of_hours({0: (10, 5.0, 30000.0)})}
        )

        get_forecast(-33.87, 151.21)

        args, kwargs = mock_get.call_args
        assert kwargs["params"]["forecast_days"] >= 2


def test_get_forecast_falls_back_to_last_entry_when_all_hours_are_past():
    hourly = _day_of_hours({-2: (11, 1.0, 30000.0), -1: (22, 2.0, 25000.0)})
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(json_body={"hourly": hourly})

        result = get_forecast(-33.87, 151.21)

    assert result == ForecastResult(
        precipitation_probability=22.0, wind_speed_kmh=2.0, visibility_m=25000.0
    )


def test_get_forecast_falls_back_to_index_zero_without_a_time_array():
    """A response missing `time` is degraded but still usable -- one point's
    weather should not fail the whole route."""
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            json_body={
                "hourly": {
                    "precipitation_probability": [72, 80],
                    "wind_speed_10m": [15.2, 18.0],
                    "visibility": [24140.0, 20000.0],
                }
            }
        )

        result = get_forecast(-33.87, 151.21)

    assert result == ForecastResult(
        precipitation_probability=72.0, wind_speed_kmh=15.2, visibility_m=24140.0
    )


def test_get_forecast_raises_request_error_on_non_200():
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(status_code=500, text="boom")

        with pytest.raises(WeatherServiceRequestError):
            get_forecast(-33.87, 151.21)


def test_get_forecast_raises_request_error_on_connection_failure():
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.side_effect = httpx.ConnectError("refused", request=_FAKE_REQUEST)

        with pytest.raises(WeatherServiceRequestError):
            get_forecast(-33.87, 151.21)


def test_get_forecast_raises_response_error_on_malformed_json():
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(text="not json")

        with pytest.raises(WeatherServiceResponseError):
            get_forecast(-33.87, 151.21)


def test_get_forecast_raises_response_error_on_missing_hourly_field():
    with patch("app.integrations.open_meteo.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(json_body={"unexpected": "shape"})

        with pytest.raises(WeatherServiceResponseError):
            get_forecast(-33.87, 151.21)
