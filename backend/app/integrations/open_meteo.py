"""Thin client for Open-Meteo's forecast endpoint. No API key required.

Mirrors app/ai/embeddings.py's shape: one function, its own error
hierarchy, the only module in this codebase that talks to Open-Meteo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_HOURLY_VARS = "precipitation_probability,wind_speed_10m,visibility"
#: Two days, not one (final-review Fix 7). `forecast_days=1` returns only
#: the CURRENT day's hours (00:00-23:00 UTC), so a request made at, say,
#: 23:30 UTC has no upcoming hour left to select at all. Two days
#: guarantees the "first hour at or after now" always exists.
_FORECAST_DAYS = 2


class WeatherServiceError(RuntimeError):
    """Base exception for get_forecast failures."""


class WeatherServiceRequestError(WeatherServiceError):
    """The HTTP call to Open-Meteo failed outright -- network error or non-2xx status."""


class WeatherServiceResponseError(WeatherServiceError):
    """Open-Meteo returned a 2xx response that isn't a usable forecast."""


@dataclass
class ForecastResult:
    precipitation_probability: float
    wind_speed_kmh: float
    visibility_m: float


def _select_current_hour_index(hourly: dict) -> int:
    """Pick the index of the first `hourly["time"]` entry at or after the
    current UTC hour.

    Final-review Fix 7: this module used to hardcode index 0 while its
    docstring claimed to return "the nearest upcoming hour". That was wrong.
    Open-Meteo's `hourly` array starts at 00:00 of the first forecast day,
    NOT at "now" -- so index 0 was up to ~24 hours stale depending on when
    the request happened to run, and a route planned at 6pm was being warned
    about this morning's weather.

    Timestamps come back as naive local-to-the-requested-timezone ISO
    strings ("2026-08-26T14:00"); with no `timezone` parameter set,
    Open-Meteo's default is GMT+0, so they are compared against UTC now.
    Falls back to the last entry if every timestamp is already in the past,
    and to index 0 if the response carries no usable `time` array at all --
    a degraded-but-usable reading beats failing the whole route."""
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        logger.warning("Open-Meteo response has no usable 'time' array; falling back to index 0")
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for index, stamp in enumerate(times):
        try:
            parsed = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        if parsed >= now.replace(minute=0, second=0, microsecond=0):
            return index

    logger.warning("Open-Meteo returned no forecast hour at or after now; using the last entry")
    return len(times) - 1


def get_forecast(
    latitude: float, longitude: float, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> ForecastResult:
    """Fetch the forecast (precipitation probability, wind speed, visibility)
    for (latitude, longitude) at the nearest upcoming hour.

    The hour is selected by matching `hourly["time"]` against the current UTC
    hour -- NOT by reading index 0, which is 00:00 of the first forecast day
    and can be nearly a full day stale (final-review Fix 7)."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": _HOURLY_VARS,
        "forecast_days": _FORECAST_DAYS,
    }

    try:
        response = httpx.get(_FORECAST_URL, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Open-Meteo request to %s failed: %s", _FORECAST_URL, exc)
        raise WeatherServiceRequestError(f"Open-Meteo request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        logger.error("Open-Meteo response was not valid JSON: %s", exc)
        raise WeatherServiceResponseError("Open-Meteo response was not valid JSON") from exc

    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(hourly, dict):
        logger.error("Open-Meteo response missing a usable 'hourly' field: %r", data)
        raise WeatherServiceResponseError("Open-Meteo response missing a usable 'hourly' field")

    index = _select_current_hour_index(hourly)

    try:
        return ForecastResult(
            precipitation_probability=float(hourly["precipitation_probability"][index]),
            wind_speed_kmh=float(hourly["wind_speed_10m"][index]),
            visibility_m=float(hourly["visibility"][index]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.error("Open-Meteo response 'hourly' field missing expected keys: %r", hourly)
        raise WeatherServiceResponseError(
            "Open-Meteo response 'hourly' field missing expected keys"
        ) from exc
