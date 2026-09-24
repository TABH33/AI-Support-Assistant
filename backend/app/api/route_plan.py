"""`POST /route-plan` -- computes a route via OpenRouteService, evaluates
weather and historical-risk-zone warnings along it, persists the result as
a `RoutePlan` row, and returns one structured result.
`GET /route-plans` and `PATCH /route-plans/{id}/complete` (Tasks 3-4) list
and complete those saved rows. `GET /route-plans/live` returns the
currently-active ones with a simulated live position computed on the fly
(nothing is stored -- see `app/ai/route_tracking.py` and
docs/superpowers/specs/2026-09-24-live-tracking-design.md). See
docs/superpowers/specs/2026-08-26-route-planning-warnings-design.md and
docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.

RBAC (mirrors every other route in this app): require_role("customer",
"support_agent"). Route/weather/risk-zone *computation* isn't
customer-owned (any authenticated user can plan a route anywhere) -- but
*persistence* is: `customer_id` resolution below mirrors
`app/api/reports.py`'s `ReportRequest` exactly (ignored for a `customer`
caller, required for a `support_agent` caller), since a saved RoutePlan row
always belongs to one customer's daily route/risk record.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.route_planning import RoutePlanResult, Warning, build_route_plan, save_route_plan
from app.ai.route_tracking import compute_route_progress
from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db
from app.integrations.openrouteservice import Coordinates
from app.models.enums import RoutePlanStatus
from app.models.route_plan import RoutePlan
from app.models.telematics import Driver
from app.security.audit import ACTION_ROUTE_PLAN_GENERATED, record_audit_event
from app.timeutil import site_day_bounds, site_today

router = APIRouter(tags=["route-plan"])

_allowed_roles = require_role("customer", "support_agent")


class CoordinatesIn(BaseModel):
    lat: float
    lon: float


class RoutePlanRequest(BaseModel):
    origin: str | CoordinatesIn
    destination: str | CoordinatesIn
    waypoints: list[str | CoordinatesIn] | None = None
    #: Only honored for a `support_agent`-role caller (required for that
    #: role -- support agents have no fleet of their own to default to).
    #: Ignored for `customer`-role callers, who are always scoped to their
    #: own JWT-derived customer_id. Mirrors `app/api/reports.py`'s
    #: `ReportRequest.customer_id` exactly.
    customer_id: int | None = None
    #: Optional driver assignment for live tracking. Validated against the
    #: RESOLVED customer's fleet (see `_resolve_driver_id`) -- a
    #: `support_agent` planning on a customer's behalf must pass a driver
    #: from that customer's fleet, not their own. Set once, here: there is
    #: deliberately no reassignment endpoint. See
    #: docs/superpowers/specs/2026-09-24-live-tracking-design.md.
    driver_id: int | None = None


class WarningOut(BaseModel):
    location: dict[str, float]
    distance_from_origin_km: float
    type: str
    severity: str
    description: str


class RoutePlanResponse(BaseModel):
    route_plan_id: int
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


class RoutePlanListItem(BaseModel):
    route_plan_id: int
    customer_id: int
    origin_label: str
    destination_label: str
    distance_km: float | None
    duration_min: float | None
    geometry: dict | None
    warnings: list[WarningOut]
    unavailable: bool
    unavailable_reason: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None


class LiveRoutePlan(RoutePlanListItem):
    """`GET /route-plans/live` row: everything `GET /route-plans` returns,
    plus the assigned driver and the simulated current position. The
    position fields are recomputed on every request from
    `(created_at, duration_min, geometry, now)` and are never persisted."""

    driver_id: int | None
    #: Joined from `Driver.full_name`; None when the plan is unassigned.
    driver_name: str | None
    current_lat: float
    current_lon: float
    progress_percent: float
    #: `created_at + duration_min` -- fixed, so it does not slide forward
    #: as time passes (same behavior as Routes.tsx's "Estimated arrival").
    eta: datetime


class RoutePlanCompleteResponse(BaseModel):
    route_plan_id: int
    status: str
    completed_at: datetime


def _to_origin_input(value: str | CoordinatesIn) -> str | Coordinates:
    if isinstance(value, CoordinatesIn):
        return Coordinates(latitude=value.lat, longitude=value.lon)
    return value


def _to_place_label(value: str | CoordinatesIn) -> str:
    if isinstance(value, CoordinatesIn):
        return f"{value.lat},{value.lon}"
    return value


def _to_warning_out(warning: Warning) -> WarningOut:
    return WarningOut(
        location={"lat": warning.latitude, "lon": warning.longitude},
        distance_from_origin_km=warning.distance_from_origin_km,
        type=warning.type,
        severity=warning.severity,
        description=warning.description,
    )


def route_plan_result_to_response(result: RoutePlanResult, *, route_plan_id: int) -> RoutePlanResponse:
    """Shared JSON-shaping function -- also called from app/api/chat.py so
    the ChatResponse.route_plan field uses the exact same shape as this
    endpoint's own response, without duplicating this mapping."""
    return RoutePlanResponse(
        route_plan_id=route_plan_id,
        distance_km=result.distance_km,
        duration_min=result.duration_min,
        geometry=result.geometry,
        warnings=[_to_warning_out(w) for w in result.warnings],
        unavailable=result.unavailable,
        unavailable_reason=result.unavailable_reason,
        unavailable_message=result.unavailable_message,
    )


def _resolve_customer_id(payload: RoutePlanRequest, current_user: CurrentUser) -> int:
    if current_user.role == "customer":
        return current_user.user_id
    if payload.customer_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="customer_id is required for a support_agent to save a route plan",
        )
    return payload.customer_id


def _resolve_driver_id(payload: RoutePlanRequest, *, customer_id: int, db: Session) -> int | None:
    """Validate that `payload.driver_id` names a `Driver` in `customer_id`'s
    own fleet, returning it (or None when no driver was given -- an
    unassigned plan still tracks).

    400, not 404: this is a malformed field on a write, so the request
    itself is rejected rather than a resource being reported missing. A
    nonexistent driver id and another customer's driver id are
    deliberately indistinguishable in the response, for the same "never
    reveal that a row exists but isn't yours" reason the read endpoints use
    404-not-403 (see `app/api/telematics.py`'s module docstring)."""
    if payload.driver_id is None:
        return None

    driver = (
        db.query(Driver)
        .filter(Driver.driver_id == payload.driver_id, Driver.customer_id == customer_id)
        .one_or_none()
    )
    if driver is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="driver_id does not name a driver in this customer's fleet",
        )
    return driver.driver_id


def _row_to_list_item(row: RoutePlan) -> RoutePlanListItem:
    return RoutePlanListItem(
        route_plan_id=row.route_plan_id,
        customer_id=row.customer_id,
        origin_label=row.origin_label,
        destination_label=row.destination_label,
        distance_km=float(row.distance_km) if row.distance_km is not None else None,
        duration_min=float(row.duration_min) if row.duration_min is not None else None,
        geometry=row.geometry,
        warnings=[WarningOut(**w) for w in row.warnings],
        unavailable=row.unavailable,
        unavailable_reason=row.unavailable_reason,
        status=row.status.value,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _geometry_points(geometry: dict | None) -> list:
    """The GeoJSON LineString coordinate list of a saved plan, or `[]` when
    there is nothing to interpolate a position along (no geometry at all,
    or an empty/malformed `coordinates`). `GET /route-plans/live` skips
    those rows rather than inventing a position for them."""
    if not isinstance(geometry, dict):
        return []
    coordinates = geometry.get("coordinates")
    return coordinates if isinstance(coordinates, list) else []


@router.post("/route-plan", response_model=RoutePlanResponse)
def post_route_plan(
    payload: RoutePlanRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> RoutePlanResponse:
    customer_id = _resolve_customer_id(payload, current_user)
    driver_id = _resolve_driver_id(payload, customer_id=customer_id, db=db)

    origin = _to_origin_input(payload.origin)
    destination = _to_origin_input(payload.destination)
    waypoints = [_to_origin_input(w) for w in payload.waypoints] if payload.waypoints else None

    result = build_route_plan(origin, destination, waypoints, db=db)

    route_plan_row = save_route_plan(
        db,
        result,
        customer_id=customer_id,
        created_by_role=current_user.role,
        created_by_id=current_user.user_id,
        origin_label=_to_place_label(payload.origin),
        destination_label=_to_place_label(payload.destination),
        driver_id=driver_id,
    )

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

    return route_plan_result_to_response(result, route_plan_id=route_plan_row.route_plan_id)


@router.get("/route-plans", response_model=list[RoutePlanListItem])
def get_route_plans(
    date_: date | None = Query(None, alias="date"),
    status_filter: RoutePlanStatus | None = Query(None, alias="status"),
    customer_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[RoutePlanListItem]:
    target_date = date_ or site_today()
    start, end = site_day_bounds(target_date)
    query = db.query(RoutePlan).filter(
        RoutePlan.created_at >= start, RoutePlan.created_at < end
    )

    if current_user.role == "customer":
        query = query.filter(RoutePlan.customer_id == current_user.user_id)
    elif customer_id is not None:
        query = query.filter(RoutePlan.customer_id == customer_id)

    if status_filter is not None:
        query = query.filter(RoutePlan.status == status_filter)

    rows = query.order_by(RoutePlan.created_at.desc()).all()
    return [_row_to_list_item(row) for row in rows]


@router.get("/route-plans/live", response_model=list[LiveRoutePlan])
def get_live_route_plans(
    customer_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[LiveRoutePlan]:
    """Every route plan currently being tracked, with a simulated live
    position computed per request.

    Scoping is identical to `GET /route-plans` above: a `customer` caller
    only ever sees their own rows (any `?customer_id=` is ignored), a
    `support_agent` sees every customer's unless narrowed by
    `?customer_id=`.

    Filtered to `status=ACTIVE AND unavailable=False` -- a completed plan
    has finished and a failed one never had a route, so neither has
    anything live to show. Unlike `GET /route-plans` there is deliberately
    no date filter: "what is moving right now" is not a per-day question,
    and a stale active plan simply pins at 100% progress until someone
    marks it complete.

    Rows whose geometry carries no coordinates are skipped rather than
    plotted at a made-up position (see `_geometry_points`).
    """
    query = (
        db.query(RoutePlan, Driver)
        .outerjoin(Driver, RoutePlan.driver_id == Driver.driver_id)
        .filter(RoutePlan.status == RoutePlanStatus.ACTIVE, RoutePlan.unavailable.is_(False))
    )

    if current_user.role == "customer":
        query = query.filter(RoutePlan.customer_id == current_user.user_id)
    elif customer_id is not None:
        query = query.filter(RoutePlan.customer_id == customer_id)

    # One `now` for the whole response, so two rows in the same payload are
    # never computed against clocks a few milliseconds apart.
    now = datetime.now(timezone.utc)

    live: list[LiveRoutePlan] = []
    for row, driver in query.order_by(RoutePlan.created_at.desc()).all():
        if not _geometry_points(row.geometry):
            continue
        progress = compute_route_progress(row, now=now)
        live.append(
            LiveRoutePlan(
                **_row_to_list_item(row).model_dump(),
                driver_id=row.driver_id,
                driver_name=driver.full_name if driver is not None else None,
                current_lat=progress.current_lat,
                current_lon=progress.current_lon,
                progress_percent=progress.progress_percent,
                eta=progress.eta,
            )
        )
    return live


@router.patch("/route-plans/{route_plan_id}/complete", response_model=RoutePlanCompleteResponse)
def complete_route_plan(
    route_plan_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> RoutePlanCompleteResponse:
    route_plan = db.get(RoutePlan, route_plan_id)
    if route_plan is None or (
        current_user.role == "customer" and route_plan.customer_id != current_user.user_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Route plan not found")
    if route_plan.status == RoutePlanStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Route plan is already completed")

    route_plan.status = RoutePlanStatus.COMPLETED
    route_plan.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(route_plan)

    return RoutePlanCompleteResponse(
        route_plan_id=route_plan.route_plan_id,
        status=route_plan.status.value,
        completed_at=route_plan.completed_at,
    )
