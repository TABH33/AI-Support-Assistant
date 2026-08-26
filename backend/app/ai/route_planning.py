"""Route-planning orchestration: turns a raw OpenRouteService route +
Open-Meteo weather + historical driving-event data into one structured
RoutePlanResult, and produces the LLM-generated natural-language summary.
Mirrors app/ai/reports.py's shape: plain, testable functions, no FastAPI
imports -- app/api/route_plan.py and app/api/chat.py are both thin callers
of build_route_plan/summarize_route_plan below.

See docs/superpowers/specs/2026-08-26-route-planning-warnings-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.geo import haversine_distance_km
from app.integrations.open_meteo import WeatherServiceError, get_forecast

DEFAULT_SAMPLE_POINT_COUNT = 8


@dataclass(frozen=True)
class SamplePoint:
    latitude: float
    longitude: float
    distance_from_origin_km: float


def sample_route_points(
    geometry: dict, target_count: int = DEFAULT_SAMPLE_POINT_COUNT
) -> list[SamplePoint]:
    """Extract target_count evenly-spaced points along a GeoJSON LineString's
    coordinates (each [longitude, latitude]), with each point's straight-line
    distance from the first coordinate (the origin). Returns every
    coordinate as-is (no sampling) if the line has target_count or fewer
    points."""
    coordinates = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
    if not coordinates:
        return []

    if len(coordinates) <= target_count:
        indices = range(len(coordinates))
    else:
        step = (len(coordinates) - 1) / (target_count - 1)
        indices = sorted({round(i * step) for i in range(target_count)})

    origin_lon, origin_lat = coordinates[0][0], coordinates[0][1]
    points = []
    for index in indices:
        lon, lat = coordinates[index][0], coordinates[index][1]
        points.append(
            SamplePoint(
                latitude=lat,
                longitude=lon,
                distance_from_origin_km=haversine_distance_km(origin_lat, origin_lon, lat, lon),
            )
        )
    return points


RISK_ZONE_RADIUS_KM = 1.0
RISK_ZONE_EVENT_THRESHOLD = 3
WEATHER_PRECIPITATION_THRESHOLD = 60.0
WEATHER_WIND_THRESHOLD_KMH = 40.0
WEATHER_VISIBILITY_THRESHOLD_M = 1000.0


@dataclass
class Warning:
    latitude: float
    longitude: float
    distance_from_origin_km: float
    type: str  # "weather" | "risk_zone"
    severity: str  # "moderate" | "high"
    description: str


def evaluate_weather_warnings(points: list[SamplePoint]) -> list[Warning]:
    """Call Open-Meteo for each sample point and flag one as a weather
    warning when precipitation probability, wind speed, or (low) visibility
    crosses a defined threshold. A single point can only produce one
    warning -- the FIRST threshold crossed wins (precipitation, then wind,
    then visibility) -- since the response is meant to be a short,
    actionable list, not one row per metric. A WeatherServiceError for one
    point is swallowed (that point is simply not flagged) rather than
    failing the whole route -- one point's transient weather-lookup failure
    should not take down the rest of the warnings list."""
    warnings: list[Warning] = []
    for point in points:
        try:
            forecast = get_forecast(point.latitude, point.longitude)
        except WeatherServiceError:
            continue

        if forecast.precipitation_probability > WEATHER_PRECIPITATION_THRESHOLD:
            warnings.append(
                Warning(
                    latitude=point.latitude,
                    longitude=point.longitude,
                    distance_from_origin_km=point.distance_from_origin_km,
                    type="weather",
                    severity="high" if forecast.precipitation_probability > 80.0 else "moderate",
                    description=(
                        f"Heavy rain forecast near this segment "
                        f"({forecast.precipitation_probability:.0f}% probability)."
                    ),
                )
            )
        elif forecast.wind_speed_kmh > WEATHER_WIND_THRESHOLD_KMH:
            warnings.append(
                Warning(
                    latitude=point.latitude,
                    longitude=point.longitude,
                    distance_from_origin_km=point.distance_from_origin_km,
                    type="weather",
                    severity="moderate",
                    description=(
                        f"Strong winds forecast near this segment "
                        f"({forecast.wind_speed_kmh:.0f} km/h)."
                    ),
                )
            )
        elif forecast.visibility_m < WEATHER_VISIBILITY_THRESHOLD_M:
            warnings.append(
                Warning(
                    latitude=point.latitude,
                    longitude=point.longitude,
                    distance_from_origin_km=point.distance_from_origin_km,
                    type="weather",
                    severity="moderate",
                    description=(
                        f"Low visibility forecast near this segment "
                        f"({forecast.visibility_m:.0f}m)."
                    ),
                )
            )
    return warnings


def evaluate_risk_zone_warnings(
    points: list[SamplePoint], *, db: Session, data_source: TelematicsDataSource | None = None
) -> list[Warning]:
    """For each sample point, look up historical DrivingEvents within
    RISK_ZONE_RADIUS_KM and flag the point as a risk zone when the event
    count reaches RISK_ZONE_EVENT_THRESHOLD. data_source defaults to
    SyntheticDataSource(db), matching every other app/ai module's pattern
    (see e.g. app/ai/retrieval.py's retrieve_context)."""
    ds = data_source if data_source is not None else SyntheticDataSource(db)

    warnings: list[Warning] = []
    for point in points:
        events = ds.get_driving_events_near(point.latitude, point.longitude, RISK_ZONE_RADIUS_KM)
        if len(events) < RISK_ZONE_EVENT_THRESHOLD:
            continue

        type_counts: dict[str, int] = {}
        for event in events:
            key = event.event_type.value
            type_counts[key] = type_counts.get(key, 0) + 1
        dominant_type, dominant_count = max(type_counts.items(), key=lambda item: item[1])

        warnings.append(
            Warning(
                latitude=point.latitude,
                longitude=point.longitude,
                distance_from_origin_km=point.distance_from_origin_km,
                type="risk_zone",
                severity="high" if len(events) >= RISK_ZONE_EVENT_THRESHOLD * 2 else "moderate",
                description=(
                    f"{len(events)} driving events recorded within "
                    f"{RISK_ZONE_RADIUS_KM:.0f}km of this point "
                    f"({dominant_count} {dominant_type.replace('_', ' ')})."
                ),
            )
        )
    return warnings
