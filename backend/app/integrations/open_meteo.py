"""Thin client for Open-Meteo's forecast endpoint. No API key required.

Mirrors app/ai/embeddings.py's shape: one function, its own error
hierarchy, the only module in this codebase that talks to Open-Meteo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_HOURLY_VARS = "precipitation_probability,wind_speed_10m,visibility"


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


def get_forecast(
    latitude: float, longitude: float, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> ForecastResult:
    """Fetch the next-hour forecast (precipitation probability, wind speed,
    visibility) for (latitude, longitude) -- the first entry of Open-Meteo's
    hourly array, i.e. the nearest upcoming hour."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": _HOURLY_VARS,
        "forecast_days": 1,
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

    try:
        return ForecastResult(
            precipitation_probability=float(hourly["precipitation_probability"][0]),
            wind_speed_kmh=float(hourly["wind_speed_10m"][0]),
            visibility_m=float(hourly["visibility"][0]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.error("Open-Meteo response 'hourly' field missing expected keys: %r", hourly)
        raise WeatherServiceResponseError(
            "Open-Meteo response 'hourly' field missing expected keys"
        ) from exc
