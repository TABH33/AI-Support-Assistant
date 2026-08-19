"""Customer model.

ER relationships owned by this entity (ASS3 Sec 3):
  Customer 1 -> Many Device
  Customer 1 -> Many ChatSession
  Customer 1 -> Many SupportTicket
  Customer 1 -> Many Driver (fleet isolation -- see telematics.py's module docstring)
  Customer 1 -> Many Vehicle (fleet isolation -- see telematics.py's module docstring)
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import PreferredNotificationMethod

if TYPE_CHECKING:
    from app.models.chat import ChatSession, SupportTicket
    from app.models.device import Device
    from app.models.telematics import Driver, Vehicle


class Customer(Base):
    """A fleet customer who owns devices and can chat with / raise tickets to support."""

    __tablename__ = "customers"

    customer_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    preferred_notification_method: Mapped[PreferredNotificationMethod] = mapped_column(
        Enum(
            PreferredNotificationMethod,
            name="preferred_notification_method",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=PreferredNotificationMethod.EMAIL,
    )
    # Present now (rather than added in the Task 6 auth task) because Task 6's own
    # brief says it "Uses Customer/SupportAgent models from Task 4" for login.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    devices: Mapped[list["Device"]] = relationship(
        "Device", back_populates="customer", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        "ChatSession", back_populates="customer", cascade="all, delete-orphan"
    )
    support_tickets: Mapped[list["SupportTicket"]] = relationship(
        "SupportTicket", back_populates="customer", cascade="all, delete-orphan"
    )
    drivers: Mapped[list["Driver"]] = relationship(
        "Driver", back_populates="customer", cascade="all, delete-orphan"
    )
    vehicles: Mapped[list["Vehicle"]] = relationship(
        "Vehicle", back_populates="customer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Customer(customer_id={self.customer_id!r}, full_name={self.full_name!r})"
