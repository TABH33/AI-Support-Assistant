"""Device model.

ER relationships owned by this entity (ASS3 Sec 3):
  Device 1 -> Many ChatSession
  Device 1 -> Many SupportTicket (via SupportTicket.device_id -- see chat.py)
(Device itself is the "Many" side of Customer 1 -> Many Device.)
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import BatteryStatus, DeviceStatus

if TYPE_CHECKING:
    from app.models.chat import ChatSession, SupportTicket
    from app.models.customer import Customer


class Device(Base):
    """A telematics device installed in a customer's vehicle.

    Fields per the authoritative source spec:
    `Device: DeviceID (PK), CustomerID (FK), SerialNumber, DeviceType,
    BatteryStatus, SignalStrength, LastSeen, DeviceStatus`.
    """

    __tablename__ = "devices"

    device_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    serial_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    device_type: Mapped[str] = mapped_column(String(64), nullable=False)
    battery_status: Mapped[BatteryStatus] = mapped_column(
        Enum(
            BatteryStatus,
            name="battery_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=BatteryStatus.OK,
    )
    # 0-100 signal strength scale (nullable: a device that has never reported
    # in has no reading yet).
    signal_strength: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_status: Mapped[DeviceStatus] = mapped_column(
        Enum(
            DeviceStatus,
            name="device_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=DeviceStatus.ACTIVE,
    )
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="devices")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        "ChatSession", back_populates="device", cascade="all, delete-orphan"
    )
    support_tickets: Mapped[list["SupportTicket"]] = relationship(
        "SupportTicket", back_populates="device"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Device(device_id={self.device_id!r}, serial_number={self.serial_number!r})"
