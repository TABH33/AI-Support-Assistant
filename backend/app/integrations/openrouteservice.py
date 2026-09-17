"""Thin client for OpenRouteService's Directions and Geocoding APIs.

Mirrors app/ai/embeddings.py's shape: one client module owning all HTTP
calls to OpenRouteService, its own error hierarchy, ORS_API_KEY read once
from app.config.settings, never hardcoded.

CRITICAL: GeoJSON/ORS coordinate order is [longitude, latitude] -- the
OPPOSITE of the (latitude, longitude) order this module's own Coordinates
dataclass uses everywhere else (matching how humans read map coordinates,
and how app.integrations.open_meteo's params are named). Every boundary
crossing in this module converts explicitly; never pass a bare [lat, lon]
or [lon, lat] list across that boundary without the conversion done here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
_DEFAULT_TIMEOUT_SECONDS = 15.0


class RouteServiceError(RuntimeError):
    """Base exception for this module's failures."""


class RouteServiceRequestError(RouteServiceError):
    """The HTTP call to OpenRouteService failed outright -- network error or non-2xx status."""


class RouteServiceResponseError(RouteServiceError):
    """OpenRouteService returned a 2xx response that isn't a usable result."""


class GeocodingError(RouteServiceError):
    """A place name could not be resolved to coordinates (no match, or a malformed response)."""


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


@dataclass
class RouteResult:
    geometry: dict
    distance_km: float
    duration_min: float


_MISSING_API_KEY_MESSAGE = "ORS_API_KEY is not configured"


def _require_api_key() -> str:
    """Honor app.config.Settings.ors_api_key's stated contract: the field
    defaults to "" (see its comment in app/config.py) and "A caller that
    actually needs it (openrouteservice.py) is responsible for treating an
    empty value as 'not configured'."

    Without this guard an unset key produced an empty Authorization header,
    ORS answered 403, and the resulting RouteServiceRequestError was
    indistinguishable in the logs from a genuine ORS outage. Raising
    RouteServiceRequestError keeps the graceful-degradation contract intact
    (build_route_plan already catches RouteServiceError and returns
    unavailable=True) while emitting a diagnosable log line and skipping a
    pointless HTTP round-trip."""
    key = settings.ors_api_key
    if not key:
        logger.error(_MISSING_API_KEY_MESSAGE)
        raise RouteServiceRequestError(_MISSING_API_KEY_MESSAGE)
    return key


def geocode(place_name: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> Coordinates:
    """Resolve a free-text place name to coordinates via ORS's Pelias-based
    geocoding endpoint. Raises GeocodingError if no match is found, or
    RouteServiceRequestError if ORS_API_KEY is not configured."""
    _require_api_key()
    params = {"text": place_name, "size": 1}
    headers = {"Authorization": settings.ors_api_key}

    try:
        response = httpx.get(_GEOCODE_URL, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("OpenRouteService geocoding request failed for %r: %s", place_name, exc)
        raise RouteServiceRequestError(
            f"OpenRouteService geocoding request failed: {exc}"
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        logger.error("OpenRouteService geocoding response was not valid JSON: %s", exc)
        raise RouteServiceResponseError(
            "OpenRouteService geocoding response was not valid JSON"
        ) from exc

    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list) or not features:
        logger.error("OpenRouteService geocoding found no match for %r", place_name)
        raise GeocodingError(f"No location found for {place_name!r}")

    try:
        # GeoJSON coordinate order is [longitude, latitude] -- swapped here
        # into this module's (latitude, longitude) Coordinates convention.
        lon, lat = features[0]["geometry"]["coordinates"][:2]
        return Coordinates(latitude=float(lat), longitude=float(lon))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        logger.error(
            "OpenRouteService geocoding response missing usable coordinates: %r", features[0]
        )
        raise RouteServiceResponseError(
            "OpenRouteService geocoding response missing usable coordinates"
        ) from exc


def get_directions(
    origin: Coordinates,
    destination: Coordinates,
    waypoints: list[Coordinates] | None = None,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> RouteResult:
    """Fetch a driving route from origin to destination (optionally via
    waypoints, in order) from OpenRouteService's Directions API. Raises
    RouteServiceRequestError if ORS_API_KEY is not configured."""
    _require_api_key()
    points = [origin, *(waypoints or []), destination]
    coordinates = [[point.longitude, point.latitude] for point in points]

    headers = {"Authorization": settings.ors_api_key, "Content-Type": "application/json"}
    body = {"coordinates": coordinates}

    try:
        response = httpx.post(_DIRECTIONS_URL, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("OpenRouteService directions request failed: %s", exc)
        raise RouteServiceRequestError(
            f"OpenRouteService directions request failed: {exc}"
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        logger.error("OpenRouteService directions response was not valid JSON: %s", exc)
        raise RouteServiceResponseError(
            "OpenRouteService directions response was not valid JSON"
        ) from exc

    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list) or not features:
        logger.error("OpenRouteService directions response had no route features: %r", data)
        raise RouteServiceResponseError(
            "OpenRouteService directions response had no route features"
        )

    feature = features[0]
    try:
        geometry = feature["geometry"]
        summary = feature["properties"]["summary"]
        distance_km = float(summary["distance"]) / 1000.0
        duration_min = float(summary["duration"]) / 60.0
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("OpenRouteService directions response missing expected fields: %r", feature)
        raise RouteServiceResponseError(
            "OpenRouteService directions response missing expected fields"
        ) from exc

    return RouteResult(geometry=geometry, distance_km=distance_km, duration_min=duration_min)
