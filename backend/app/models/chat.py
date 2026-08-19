"""ChatSession, SupportTicket, and Notification models.

ER relationships owned by these entities (ASS3 Sec 3):
  ChatSession 1 -> 0..1 SupportTicket
  SupportTicket 1 -> Many Notification
(ChatSession and SupportTicket are also the "Many" side of Customer's two
1 -> Many relationships, and ChatSession is the "Many" side of Device's
1 -> Many relationship; SupportTicket is the "Many" side of SupportAgent's
1 -> Many relationship.)

Note on `ai_confidence_score`: the Global Constraints example field list orders
`AIConfidenceScore` immediately before `SessionStatus` and after Customer's
fields, which reads as grouping it with ChatSession (the AI's confidence in
its handling of that session) rather than SupportTicket. It is modeled here on
ChatSession accordingly -- see task report for this interpretation call.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import PreferredNotificationMethod, Priority, SessionStatus, TicketStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.device import Device
    from app.models.support_agent import SupportAgent


class ChatSession(Base):
    """A single chat conversation between a customer (via a device context) and the AI assistant."""

    __tablename__ = "chat_sessions"

    chat_session_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.device_id"), nullable=False)
    session_status: Mapped[SessionStatus] = mapped_column(
        Enum(
            SessionStatus,
            name="session_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="chat_sessions")
    device: Mapped["Device"] = relationship("Device", back_populates="chat_sessions")
    support_ticket: Mapped["SupportTicket | None"] = relationship(
        "SupportTicket", back_populates="chat_session", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ChatSession(chat_session_id={self.chat_session_id!r}, session_status={self.session_status!r})"


class SupportTicket(Base):
    """A human-support escalation, created from at most one ChatSession."""

    __tablename__ = "support_tickets"

    support_ticket_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.chat_session_id"), nullable=False, unique=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    assigned_support_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("support_agents.support_agent_id"), nullable=True
    )
    ticket_status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=TicketStatus.OPEN,
    )
    priority: Mapped[Priority] = mapped_column(
        Enum(
            Priority,
            name="priority",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=Priority.MEDIUM,
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chat_session: Mapped["ChatSession"] = relationship(
        "ChatSession", back_populates="support_ticket"
    )
    customer: Mapped["Customer"] = relationship("Customer", back_populates="support_tickets")
    assigned_support_agent: Mapped["SupportAgent | None"] = relationship(
        "SupportAgent", back_populates="assigned_tickets"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="support_ticket", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"SupportTicket(support_ticket_id={self.support_ticket_id!r}, ticket_status={self.ticket_status!r})"


class Notification(Base):
    """A notification sent to a customer about a SupportTicket's progress."""

    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    support_ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.support_ticket_id"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    notification_type: Mapped[PreferredNotificationMethod] = mapped_column(
        Enum(
            PreferredNotificationMethod,
            name="preferred_notification_method",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    support_ticket: Mapped["SupportTicket"] = relationship(
        "SupportTicket", back_populates="notifications"
    )
    customer: Mapped["Customer"] = relationship("Customer")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Notification(notification_id={self.notification_id!r}, support_ticket_id={self.support_ticket_id!r})"
