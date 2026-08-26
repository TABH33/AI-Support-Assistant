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

from app.geo import haversine_distance_km

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
