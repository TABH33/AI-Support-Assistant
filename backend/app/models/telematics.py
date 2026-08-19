"""Driver, Vehicle, Trip, and DrivingEvent models (ASS2 class diagram).

ER relationships owned by these entities:
  Driver 1 -> Many Trip
  Trip -> Vehicle (many trips reference one vehicle)
  Trip 1 -> Many DrivingEvent

Note: the plan's Global Constraints / ER relationship list does not specify a
Customer -> Driver or Customer -> Vehicle relationship, so none is added here
-- only the relationships explicitly listed in the task brief are modeled.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import DrivingEventType


class Driver(Base):
    """A driver who operates vehicles and generates trips."""

    __tablename__ = "drivers"

    driver_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    license_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    trips: Mapped[list["Trip"]] = relationship(
        "Trip", back_populates="driver", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Driver(driver_id={self.driver_id!r}, full_name={self.full_name!r})"


class Vehicle(Base):
    """A fleet vehicle that trips are recorded against."""

    __tablename__ = "vehicles"

    vehicle_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    registration_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    make: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    year: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    trips: Mapped[list["Trip"]] = relationship(
        "Trip", back_populates="vehicle", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Vehicle(vehicle_id={self.vehicle_id!r}, registration_number={self.registration_number!r})"


class Trip(Base):
    """A single trip made by a driver in a vehicle, containing driving events."""

    __tablename__ = "trips"

    trip_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.driver_id"), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.vehicle_id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    end_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    driver: Mapped["Driver"] = relationship("Driver", back_populates="trips")
    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="trips")
    driving_events: Mapped[list["DrivingEvent"]] = relationship(
        "DrivingEvent", back_populates="trip", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Trip(trip_id={self.trip_id!r}, driver_id={self.driver_id!r}, vehicle_id={self.vehicle_id!r})"


class DrivingEvent(Base):
    """A discrete driving event (speeding, harsh braking, idling, route deviation)
    detected within a trip -- matches Task 5's synthetic-generator event types."""

    __tablename__ = "driving_events"

    driving_event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.trip_id"), nullable=False)
    event_type: Mapped[DrivingEventType] = mapped_column(
        Enum(
            DrivingEventType,
            name="driving_event_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    trip: Mapped["Trip"] = relationship("Trip", back_populates="driving_events")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"DrivingEvent(driving_event_id={self.driving_event_id!r}, event_type={self.event_type!r})"
