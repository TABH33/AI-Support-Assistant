"""Tests for app.integrations.open_meteo."""
from __future__ import annotations

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


def test_get_forecast_returns_parsed_result():
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
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["latitude"] == -33.87
        assert kwargs["params"]["longitude"] == 151.21


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
