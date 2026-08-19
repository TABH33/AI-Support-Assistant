"""Read-only telematics CRUD APIs: drivers, vehicles, trips, driving events,
and devices.

RBAC (per the plan's global constraints): every route here sits behind
`require_role("customer", "support_agent")`. A `customer`-role caller only
ever sees rows belonging to *their own* customer account -- enforced by
filtering every query against `current_user.user_id` (the JWT-derived
customer_id from `get_current_user`), never a client-supplied value. A
`support_agent`-role caller is unrestricted (support agents need visibility
across every customer to help any of them).

Customer scoping, by entity (see `backend/app/models/telematics.py`'s
module docstring for how this was decided -- `Driver`/`Vehicle` gained a
direct `customer_id` FK in a Task 4 follow-up migration specifically for
this per-customer-fleet-isolation requirement):

  * Device: `Device.customer_id` directly.
  * Driver:  `Driver.customer_id` directly.
  * Vehicle: `Vehicle.customer_id` directly.
  * Trip:    no `customer_id` column of its own -- scoped transitively via
    `Trip.driver.customer_id` (data-layer guarantees a trip's driver and
    vehicle always belong to the same customer, so joining through either
    would give the same result; the driver join is used here).
  * DrivingEvent: scoped transitively via its parent Trip's driver, i.e.
    "can this caller see the parent trip" gates "can they see its events".

A customer-scoped resource that exists but doesn't belong to the caller
returns 404 (not 403), so a customer can't distinguish "not found" from
"exists but isn't yours" -- avoids leaking cross-customer existence.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Query as SAQuery, Session

from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db
from app.models.device import Device
from app.models.enums import BatteryStatus, DeviceStatus, DrivingEventType
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle

router = APIRouter(tags=["telematics"])

_allowed_roles = require_role("customer", "support_agent")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DriverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    driver_id: int
    customer_id: int
    full_name: str
    license_number: str
    email: str | None
    phone_number: str | None
    created_at: datetime


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: int
    customer_id: int
    registration_number: str
    make: str
    model: str
    year: int
    created_at: datetime


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: int
    driver_id: int
    vehicle_id: int
    start_time: datetime
    end_time: datetime | None
    start_location: str | None
    end_location: str | None
    distance_km: float | None
    created_at: datetime


class DrivingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    driving_event_id: int
    trip_id: int
    event_type: DrivingEventType
    event_time: datetime
    location: str | None
    details: str | None
    created_at: datetime


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    customer_id: int
    serial_number: str
    device_type: str
    battery_status: BatteryStatus
    signal_strength: int | None
    last_seen: datetime | None
    device_status: DeviceStatus
    installed_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Scoping helpers -- shared query-building logic, reused across endpoints
# ---------------------------------------------------------------------------


def _scoped_drivers(db: Session, current_user: CurrentUser) -> SAQuery:
    query = db.query(Driver)
    if current_user.role == "customer":
        query = query.filter(Driver.customer_id == current_user.user_id)
    return query


def _scoped_vehicles(db: Session, current_user: CurrentUser) -> SAQuery:
    query = db.query(Vehicle)
    if current_user.role == "customer":
        query = query.filter(Vehicle.customer_id == current_user.user_id)
    return query


def _scoped_trips(db: Session, current_user: CurrentUser) -> SAQuery:
    """Trips have no `customer_id` of their own -- scope transitively by
    joining to their driver (data layer guarantees driver.customer_id ==
    vehicle.customer_id for any given trip, so either join works)."""
    query = db.query(Trip).join(Driver, Trip.driver_id == Driver.driver_id)
    if current_user.role == "customer":
        query = query.filter(Driver.customer_id == current_user.user_id)
    return query


def _scoped_devices(db: Session, current_user: CurrentUser) -> SAQuery:
    query = db.query(Device)
    if current_user.role == "customer":
        query = query.filter(Device.customer_id == current_user.user_id)
    return query


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


@router.get("/drivers", response_model=list[DriverOut])
def list_drivers(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[Driver]:
    return _scoped_drivers(db, current_user).order_by(Driver.driver_id).all()


@router.get("/drivers/{driver_id}", response_model=DriverOut)
def get_driver(
    driver_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> Driver:
    driver = _scoped_drivers(db, current_user).filter(Driver.driver_id == driver_id).one_or_none()
    if driver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Driver not found")
    return driver


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------


@router.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[Vehicle]:
    return _scoped_vehicles(db, current_user).order_by(Vehicle.vehicle_id).all()


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------


@router.get("/trips", response_model=list[TripOut])
def list_trips(
    driver_id: int | None = Query(default=None),
    vehicle_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[Trip]:
    query = _scoped_trips(db, current_user)
    if driver_id is not None:
        query = query.filter(Trip.driver_id == driver_id)
    if vehicle_id is not None:
        query = query.filter(Trip.vehicle_id == vehicle_id)
    return query.order_by(Trip.trip_id).all()


@router.get("/trips/{trip_id}/events", response_model=list[DrivingEventOut])
def list_trip_events(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[DrivingEvent]:
    trip = _scoped_trips(db, current_user).filter(Trip.trip_id == trip_id).one_or_none()
    if trip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return (
        db.query(DrivingEvent)
        .filter(DrivingEvent.trip_id == trip_id)
        .order_by(DrivingEvent.event_time)
        .all()
    )


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(
    customer_id: int | None = Query(
        default=None,
        description=(
            "Filter by customer. Ignored for `customer`-role callers (who "
            "are always scoped to their own customer_id); optional for "
            "`support_agent` callers, who see all customers' devices when "
            "omitted."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[Device]:
    query = _scoped_devices(db, current_user)
    if current_user.role == "support_agent" and customer_id is not None:
        query = query.filter(Device.customer_id == customer_id)
    return query.order_by(Device.device_id).all()
