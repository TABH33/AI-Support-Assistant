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

from app.ai.llm import chat_completion
from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.geo import haversine_distance_km
from app.integrations.open_meteo import WeatherServiceError, get_forecast
from app.integrations.openrouteservice import (
    Coordinates,
    RouteServiceError,
    geocode,
    get_directions,
)

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


ROUTE_DATA_UNAVAILABLE_TEXT = (
    "I can't retrieve route data right now -- please try again shortly."
)


@dataclass
class RoutePlanResult:
    distance_km: float | None
    duration_min: float | None
    geometry: dict | None
    warnings: list[Warning] = field(default_factory=list)
    unavailable: bool = False


def build_route_plan(
    origin: str | Coordinates,
    destination: str | Coordinates,
    waypoints: list[str | Coordinates] | None = None,
    *,
    db: Session,
    data_source: TelematicsDataSource | None = None,
) -> RoutePlanResult:
    """Top-level entry point: resolves origin/destination/waypoints
    (geocoding any plain string), fetches the route, samples points along
    it, evaluates both warning types, and returns one RoutePlanResult. On
    any RouteServiceError (geocoding or directions failure), returns
    unavailable=True instead of raising -- callers (POST /route-plan, the
    chat route-plan intent) must never crash on a downstream API outage."""
    try:
        origin_coords = geocode(origin) if isinstance(origin, str) else origin
        destination_coords = geocode(destination) if isinstance(destination, str) else destination
        waypoint_coords = (
            [geocode(w) if isinstance(w, str) else w for w in waypoints] if waypoints else None
        )
        route = get_directions(origin_coords, destination_coords, waypoint_coords)
    except RouteServiceError:
        return RoutePlanResult(distance_km=None, duration_min=None, geometry=None, unavailable=True)

    points = sample_route_points(route.geometry)
    warnings = evaluate_weather_warnings(points) + evaluate_risk_zone_warnings(
        points, db=db, data_source=data_source
    )

    return RoutePlanResult(
        distance_km=route.distance_km,
        duration_min=route.duration_min,
        geometry=route.geometry,
        warnings=warnings,
    )


def build_route_summary_prompt(
    route_plan: RoutePlanResult, origin_label: str, destination_label: str
) -> list[dict[str, str]]:
    """Build the system/user messages for the LLM's natural-language route
    summary. Only meaningful when route_plan.unavailable is False -- callers
    must check that before calling this."""
    warning_lines = (
        "\n".join(
            f"- {w.type} warning at {w.distance_from_origin_km:.1f}km from origin "
            f"(severity: {w.severity}): {w.description}"
            for w in route_plan.warnings
        )
        or "(no warnings flagged on this route)"
    )
    system_prompt = (
        "You are a support assistant for a fleet telematics platform. "
        "Summarize the route data given below in plain, natural language for "
        "a fleet driver or dispatcher. Use ONLY the numbers and warnings "
        "given -- never invent a distance, duration, or warning not listed. "
        "Be concise: state the distance and duration, then call out any "
        "warnings and a brief recommendation (e.g. reduced speed near a risk "
        "zone, or allowing extra time for weather)."
    )
    user_content = (
        f"Route: {origin_label} to {destination_label}\n"
        f"Distance: {route_plan.distance_km:.1f} km\n"
        f"Duration: {route_plan.duration_min:.0f} minutes\n"
        f"Warnings:\n{warning_lines}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def summarize_route_plan(
    route_plan: RoutePlanResult, origin_label: str, destination_label: str
) -> str:
    """Build the route summary prompt and call the LLM, returning the
    natural-language summary text. Mirrors app/ai/reports.py's pattern of
    owning its own LLM call internally rather than exposing chat_completion
    to callers. Only meaningful when route_plan.unavailable is False --
    callers must check that first."""
    messages = build_route_summary_prompt(route_plan, origin_label, destination_label)
    return chat_completion(messages)
