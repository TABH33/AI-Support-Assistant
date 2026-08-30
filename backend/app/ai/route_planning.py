"""Route-planning orchestration: turns a raw OpenRouteService route +
Open-Meteo weather + historical driving-event data into one structured
RoutePlanResult, and produces the LLM-generated natural-language summary.
Mirrors app/ai/reports.py's shape: plain, testable functions, no FastAPI
imports -- app/api/route_plan.py and app/api/chat.py are both thin callers
of build_route_plan/summarize_route_plan below.

See docs/superpowers/specs/2026-08-26-route-planning-warnings-design.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.llm import chat_completion
from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.geo import haversine_distance_km
from app.integrations.open_meteo import WeatherServiceError, get_forecast
from app.integrations.openrouteservice import (
    Coordinates,
    GeocodingError,
    RouteServiceError,
    geocode,
    get_directions,
)

logger = logging.getLogger(__name__)

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


# Risk-zone tuning (final-review Fix 3). The original 1.0km / 3-event pair
# was calibrated against no data: the real seed set (200 DrivingEvents
# spread over the 3 short DEMO_CORRIDORS, app/seed/generator.py) puts 3-27
# events within 1km of EVERY sampled point on EVERY demo corridor, so all 8
# sample points flagged on all 3 routes. A "risk zone" covering 100% of
# every route carries no information at all, defeating the design's
# "deterministic, selective signal" goal.
#
# These values were chosen by probing the real generated seed data
# (generate_seed_data(), fixed DEFAULT_SEED=1337) at 8 evenly-spaced points
# along each demo corridor. At 0.5km / 10 events the flagged-point counts
# are: Sydney CBD->Parramatta 1/8, Sydney CBD->Sydney Airport 2/8, Sydney
# CBD->Bondi Beach 3/8. The Parramatta corridor is ~19km (vs ~8km and ~6km
# for the other two) so its events are genuinely sparser; no (radius,
# threshold) pair in the whole 0.25-1.0km x 3-30 event search space reaches
# 2/8 there without pushing the other two corridors to 5/8 or more, so 1/8
# is the tightest achievable balance -- selective on every corridor, and
# never zero.
RISK_ZONE_RADIUS_KM = 0.5
RISK_ZONE_EVENT_THRESHOLD = 10
# Severity split, previously the inline expression `THRESHOLD * 2`. With
# THRESHOLD raised to 10 that formula would require 20 events within 500m --
# more than the densest point on any demo corridor (14), making "high"
# severity unreachable dead code. 14 is the observed top of the real
# distribution, so exactly the worst point on the worst corridor reads as
# high and the rest read as moderate.
RISK_ZONE_HIGH_SEVERITY_THRESHOLD = 14
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
    count reaches RISK_ZONE_EVENT_THRESHOLD, escalating to "high" severity
    at RISK_ZONE_HIGH_SEVERITY_THRESHOLD. data_source defaults to
    SyntheticDataSource(db), matching every other app/ai module's pattern
    (see e.g. app/ai/retrieval.py's retrieve_context).

    Raises whatever the data source raises -- notably SQLAlchemyError on a
    DB failure. build_route_plan is responsible for catching that and
    degrading to unavailable=True (final-review Fix 6a); a direct caller
    must handle it itself."""
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
                severity=(
                    "high"
                    if len(events) >= RISK_ZONE_HIGH_SEVERITY_THRESHOLD
                    else "moderate"
                ),
                description=(
                    # Rendered in metres, not `{RISK_ZONE_RADIUS_KM:.0f}km`:
                    # that format produced the literal text "0km" once the
                    # radius dropped below 0.5 (final-review Fix 3).
                    f"{len(events)} driving events recorded within "
                    f"{RISK_ZONE_RADIUS_KM * 1000:.0f}m of this point "
                    f"({dominant_count} {dominant_type.replace('_', ' ')})."
                ),
            )
        )
    return warnings


ROUTE_DATA_UNAVAILABLE_TEXT = (
    "I can't retrieve route data right now -- please try again shortly."
)
#: Final-review Fix 5: an unresolvable place name is NOT a service outage.
#: Telling a user who typed "Parramattaa" to "try again shortly" is useless
#: advice -- retrying cannot fix a misspelling. `{place}` is filled with the
#: exact string that failed to geocode.
GEOCODING_FAILED_TEXT = (
    "I couldn't find a location matching '{place}' -- please check the "
    "spelling or try a more specific name."
)

#: Machine-readable values for RoutePlanResult.unavailable_reason. Callers
#: that only need something to show a human should use
#: `unavailable_message` instead.
UNAVAILABLE_REASON_SERVICE = "service_unavailable"
UNAVAILABLE_REASON_GEOCODING = "geocoding_failed"


@dataclass
class RoutePlanResult:
    distance_km: float | None
    duration_min: float | None
    geometry: dict | None
    warnings: list[Warning] = field(default_factory=list)
    unavailable: bool = False
    #: Which failure mode produced `unavailable=True` -- one of the
    #: UNAVAILABLE_REASON_* constants above, or None when the plan succeeded.
    #: Separate from `unavailable_message` so the HTTP response can carry a
    #: stable machine-readable code without clients parsing prose.
    unavailable_reason: str | None = None
    #: The ready-to-display text for this failure mode. None when the plan
    #: succeeded. Lives here rather than being reconstructed by each caller
    #: because the geocoding message embeds the specific place that failed,
    #: which only build_route_plan knows.
    unavailable_message: str | None = None


def _unavailable(reason: str, message: str) -> RoutePlanResult:
    return RoutePlanResult(
        distance_km=None,
        duration_min=None,
        geometry=None,
        unavailable=True,
        unavailable_reason=reason,
        unavailable_message=message,
    )


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
    it, evaluates both warning types, and returns one RoutePlanResult.

    Never raises on a downstream failure -- callers (POST /route-plan, the
    chat route-plan intent) must never 5xx because an external API or the
    database misbehaved. Three failure modes all degrade to
    unavailable=True, distinguished by `unavailable_reason`:

      * GeocodingError -> UNAVAILABLE_REASON_GEOCODING. Caught BEFORE the
        RouteServiceError parent it inherits from (final-review Fix 5), so
        an unresolvable place name gets a "check the spelling" message
        naming that place rather than the generic "try again shortly",
        which is actively misleading advice for a typo.
      * Any other RouteServiceError -> UNAVAILABLE_REASON_SERVICE.
      * SQLAlchemyError from the warning evaluation below ->
        UNAVAILABLE_REASON_SERVICE (final-review Fix 6a).
    """
    resolved: list[Coordinates] = []
    try:
        for place in [origin, destination, *(waypoints or [])]:
            if not isinstance(place, str):
                resolved.append(place)
                continue
            try:
                resolved.append(geocode(place))
            except GeocodingError:
                # More specific than the RouteServiceError handler below,
                # and handled here (rather than as a second `except` clause)
                # so the failing place name is still in scope to name it.
                return _unavailable(
                    UNAVAILABLE_REASON_GEOCODING, GEOCODING_FAILED_TEXT.format(place=place)
                )

        origin_coords, destination_coords = resolved[0], resolved[1]
        waypoint_coords = resolved[2:] or None
        route = get_directions(origin_coords, destination_coords, waypoint_coords)
    except RouteServiceError:
        return _unavailable(UNAVAILABLE_REASON_SERVICE, ROUTE_DATA_UNAVAILABLE_TEXT)

    points = sample_route_points(route.geometry)
    try:
        # evaluate_risk_zone_warnings issues real DB queries via
        # SyntheticDataSource. Final-review Fix 6a: that call used to sit
        # outside every try block, so a DB failure here propagated as an
        # unhandled exception through POST /route-plan and POST /chat and
        # became a 500 -- violating this feature's "never 5xx on a
        # downstream failure" contract for exactly the same reason the ORS
        # and Open-Meteo calls are already guarded.
        #
        # SQLAlchemyError is the right width: it is the common base for
        # every DB-layer failure (OperationalError for a dropped
        # connection, DBAPIError, InvalidRequestError for a broken
        # session), so it covers the realistic outage modes, while staying
        # narrow enough that a genuine bug in this module's own logic (a
        # TypeError, a KeyError) still surfaces loudly as a 500 instead of
        # being silently reported to the user as "route data unavailable".
        # evaluate_weather_warnings is inside the same block only for
        # symmetry -- it already swallows WeatherServiceError per point.
        warnings = evaluate_weather_warnings(points) + evaluate_risk_zone_warnings(
            points, db=db, data_source=data_source
        )
    except SQLAlchemyError:
        logger.exception("Route-plan warning evaluation failed on a database error")
        return _unavailable(UNAVAILABLE_REASON_SERVICE, ROUTE_DATA_UNAVAILABLE_TEXT)

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
