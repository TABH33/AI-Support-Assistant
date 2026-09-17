"""`POST /route-plan` -- computes a route via OpenRouteService, evaluates
weather and historical-risk-zone warnings along it, and returns one
structured result. See
docs/superpowers/specs/2026-08-26-route-planning-warnings-design.md.

RBAC (mirrors every other route in this app): require_role("customer",
"support_agent"). Route/weather/risk-zone data isn't customer-owned, so no
customer_id scoping is needed for this endpoint's own logic -- the RBAC
gate is for basic authenticated access only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.route_planning import RoutePlanResult, Warning, build_route_plan
from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db
from app.integrations.openrouteservice import Coordinates
from app.security.audit import ACTION_ROUTE_PLAN_GENERATED, record_audit_event

router = APIRouter(tags=["route-plan"])

_allowed_roles = require_role("customer", "support_agent")


class CoordinatesIn(BaseModel):
    lat: float
    lon: float


class RoutePlanRequest(BaseModel):
    origin: str | CoordinatesIn
    destination: str | CoordinatesIn
    waypoints: list[str | CoordinatesIn] | None = None


class WarningOut(BaseModel):
    location: dict[str, float]
    distance_from_origin_km: float
    type: str
    severity: str
    description: str


class RoutePlanResponse(BaseModel):
    distance_km: float | None
    duration_min: float | None
    geometry: dict | None
    warnings: list[WarningOut]
    unavailable: bool
    #: Final-review Fix 5. When `unavailable` is True these say WHY, so a
    #: client can distinguish "that place name doesn't exist" (retrying is
    #: pointless -- fix the spelling) from "the routing service is down"
    #: (retrying shortly is exactly the right advice). Both are None on a
    #: successful plan. `unavailable_reason` is a stable machine-readable
    #: code (see route_planning.UNAVAILABLE_REASON_*);
    #: `unavailable_message` is the display text.
    unavailable_reason: str | None = None
    unavailable_message: str | None = None


def _to_origin_input(value: str | CoordinatesIn) -> str | Coordinates:
    if isinstance(value, CoordinatesIn):
        return Coordinates(latitude=value.lat, longitude=value.lon)
    return value


def _to_warning_out(warning: Warning) -> WarningOut:
    return WarningOut(
        location={"lat": warning.latitude, "lon": warning.longitude},
        distance_from_origin_km=warning.distance_from_origin_km,
        type=warning.type,
        severity=warning.severity,
        description=warning.description,
    )


def route_plan_result_to_response(result: RoutePlanResult) -> RoutePlanResponse:
    """Shared JSON-shaping function -- also called from app/api/chat.py so
    the ChatResponse.route_plan field uses the exact same shape as this
    endpoint's own response, without duplicating this mapping."""
    return RoutePlanResponse(
        distance_km=result.distance_km,
        duration_min=result.duration_min,
        geometry=result.geometry,
        warnings=[_to_warning_out(w) for w in result.warnings],
        unavailable=result.unavailable,
        unavailable_reason=result.unavailable_reason,
        unavailable_message=result.unavailable_message,
    )


@router.post("/route-plan", response_model=RoutePlanResponse)
def post_route_plan(
    payload: RoutePlanRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> RoutePlanResponse:
    origin = _to_origin_input(payload.origin)
    destination = _to_origin_input(payload.destination)
    waypoints = [_to_origin_input(w) for w in payload.waypoints] if payload.waypoints else None

    result = build_route_plan(origin, destination, waypoints, db=db)

    record_audit_event(
        db,
        actor_id=current_user.user_id,
        actor_role=current_user.role,
        action=ACTION_ROUTE_PLAN_GENERATED,
        description=(
            f"origin={payload.origin!r} destination={payload.destination!r} "
            f"unavailable={result.unavailable} reason={result.unavailable_reason}"
        ),
    )
    db.commit()

    return route_plan_result_to_response(result)
