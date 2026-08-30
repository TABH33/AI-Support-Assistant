"""Tests for app.integrations.openrouteservice."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.config import settings
from app.integrations.openrouteservice import (
    Coordinates,
    GeocodingError,
    RouteResult,
    RouteServiceRequestError,
    RouteServiceResponseError,
    geocode,
    get_directions,
)

_FAKE_GEOCODE_REQUEST = httpx.Request("GET", "https://api.openrouteservice.org/geocode/search")
_FAKE_DIRECTIONS_REQUEST = httpx.Request(
    "POST", "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
)


def _mock_response(request, status_code=200, json_body=None, text=None):
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, text=text or "", request=request)


@pytest.fixture(autouse=True)
def configured_api_key(monkeypatch):
    """`Settings.ors_api_key` defaults to "" and nothing sets it in the test
    environment. Final-review Fix 2 makes an empty key a hard "not
    configured" failure before any HTTP call, so every test in this module
    that exercises the *HTTP* behavior needs a non-empty key. The
    missing-key tests below opt out by monkeypatching it back to ""."""
    monkeypatch.setattr(settings, "ors_api_key", "test-ors-key", raising=False)


# ---------------------------------------------------------------------------
# Missing-API-key guard (final-review Fix 2)
# ---------------------------------------------------------------------------


def test_geocode_raises_request_error_when_api_key_not_configured(monkeypatch):
    """An unset ORS_API_KEY must fail fast with a diagnosable error rather
    than sending an empty Authorization header and getting back a 403 that
    is indistinguishable from a genuine ORS outage."""
    monkeypatch.setattr(settings, "ors_api_key", "")
    with patch("app.integrations.openrouteservice.httpx.get") as mock_get:
        with pytest.raises(RouteServiceRequestError, match="ORS_API_KEY is not configured"):
            geocode("Sydney CBD")

        mock_get.assert_not_called()


def test_get_directions_raises_request_error_when_api_key_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "ors_api_key", "")
    with patch("app.integrations.openrouteservice.httpx.post") as mock_post:
        with pytest.raises(RouteServiceRequestError, match="ORS_API_KEY is not configured"):
            get_directions(
                Coordinates(latitude=-33.8688, longitude=151.2093),
                Coordinates(latitude=-33.8150, longitude=151.0011),
            )

        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# geocode
# ---------------------------------------------------------------------------


def test_geocode_returns_coordinates_from_first_match():
    with patch("app.integrations.openrouteservice.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            _FAKE_GEOCODE_REQUEST,
            json_body={"features": [{"geometry": {"coordinates": [151.2093, -33.8688]}}]},
        )

        result = geocode("Sydney CBD")

        assert result == Coordinates(latitude=-33.8688, longitude=151.2093)


def test_geocode_sends_api_key_via_header_not_query_param():
    """Regression test: the API key must never appear in the request URL/params,
    since httpx.HTTPStatusError's str() includes the full URL, and that string
    can end up in logs or exception messages on a non-2xx response."""
    with patch("app.integrations.openrouteservice.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            _FAKE_GEOCODE_REQUEST,
            json_body={"features": [{"geometry": {"coordinates": [151.2093, -33.8688]}}]},
        )

        geocode("Sydney CBD")

        args, kwargs = mock_get.call_args
        assert "api_key" not in kwargs["params"]
        assert kwargs["headers"]["Authorization"] == settings.ors_api_key


def test_geocode_raises_geocoding_error_on_no_match():
    with patch("app.integrations.openrouteservice.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FAKE_GEOCODE_REQUEST, json_body={"features": []})

        with pytest.raises(GeocodingError):
            geocode("Nowhere Place XYZ")


def test_geocode_raises_request_error_on_non_200():
    with patch("app.integrations.openrouteservice.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            _FAKE_GEOCODE_REQUEST, status_code=401, text="unauthorized"
        )

        with pytest.raises(RouteServiceRequestError):
            geocode("Sydney CBD")


def test_geocode_raises_response_error_on_malformed_json():
    with patch("app.integrations.openrouteservice.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FAKE_GEOCODE_REQUEST, text="not json")

        with pytest.raises(RouteServiceResponseError):
            geocode("Sydney CBD")


# ---------------------------------------------------------------------------
# get_directions
# ---------------------------------------------------------------------------

_ORIGIN = Coordinates(latitude=-33.8688, longitude=151.2093)
_DESTINATION = Coordinates(latitude=-33.8150, longitude=151.0011)
_FAKE_GEOMETRY = {"type": "LineString", "coordinates": [[151.2093, -33.8688], [151.0011, -33.8150]]}


def test_get_directions_returns_parsed_route():
    with patch("app.integrations.openrouteservice.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(
            _FAKE_DIRECTIONS_REQUEST,
            json_body={
                "features": [
                    {
                        "geometry": _FAKE_GEOMETRY,
                        "properties": {"summary": {"distance": 23400.0, "duration": 2292.0}},
                    }
                ]
            },
        )

        result = get_directions(_ORIGIN, _DESTINATION)

        assert result == RouteResult(geometry=_FAKE_GEOMETRY, distance_km=23.4, duration_min=38.2)
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["coordinates"] == [[151.2093, -33.8688], [151.0011, -33.8150]]


def test_get_directions_includes_waypoints_in_order():
    waypoint = Coordinates(latitude=-33.85, longitude=151.10)
    with patch("app.integrations.openrouteservice.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(
            _FAKE_DIRECTIONS_REQUEST,
            json_body={
                "features": [
                    {
                        "geometry": _FAKE_GEOMETRY,
                        "properties": {"summary": {"distance": 23400.0, "duration": 2292.0}},
                    }
                ]
            },
        )

        get_directions(_ORIGIN, _DESTINATION, waypoints=[waypoint])

        args, kwargs = mock_post.call_args
        assert kwargs["json"]["coordinates"] == [
            [151.2093, -33.8688],
            [151.10, -33.85],
            [151.0011, -33.8150],
        ]


def test_get_directions_raises_request_error_on_non_200():
    with patch("app.integrations.openrouteservice.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(
            _FAKE_DIRECTIONS_REQUEST, status_code=500, text="boom"
        )

        with pytest.raises(RouteServiceRequestError):
            get_directions(_ORIGIN, _DESTINATION)


def test_get_directions_raises_response_error_on_no_features():
    with patch("app.integrations.openrouteservice.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(_FAKE_DIRECTIONS_REQUEST, json_body={"features": []})

        with pytest.raises(RouteServiceResponseError):
            get_directions(_ORIGIN, _DESTINATION)
