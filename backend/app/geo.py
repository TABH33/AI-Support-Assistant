"""Shared geographic distance helper.

A single pure function used by both app/datasources/synthetic.py
(risk-zone radius filtering) and app/ai/route_planning.py (route point
sampling) -- pulled out here specifically because it has two real
consumers, not as speculative shared-utility scaffolding.
"""
from __future__ import annotations

import math

_EARTH_RADIUS_KM = 6371.0


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
