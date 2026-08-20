"""ChatSession, SupportTicket, and Notification models.

ER relationships owned by these entities (ASS3 Sec 3):
  ChatSession 1 -> 0..1 SupportTicket
  SupportTicket 1 -> Many Notification
(ChatSession and SupportTicket are also the "Many" side of Customer's two
1 -> Many relationships, and ChatSession is the "Many" side of Device's
1 -> Many relationship; SupportTicket is the "Many" side of SupportAgent's
1 -> Many relationship. SupportTicket also carries a direct `device_id` FK
per the authoritative field-level spec -- `SupportTicket: TicketID (PK),
CustomerID (FK), DeviceID (FK), ChatSessionID (FK), TicketStatus, Priority,
CreatedDate, ClosedDate, AssignedSupportAgent` -- even though the task
brief's relationship-cardinality summary didn't separately list a
Device -> SupportTicket relationship.)

Note on `ai_confidence_score`: the Global Constraints example field list orders
`AIConfidenceScore` immediately before `SessionStatus` and after Customer's
fields, which reads as grouping it with ChatSession (the AI's confidence in
its handling of that session) rather than SupportTicket. It is modeled here on
ChatSession accordingly -- see task report for this interpretation call.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import (
    ChatMessageRole,
    PreferredNotificationMethod,
    Priority,
    SessionStatus,
    TicketStatus,
)

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
    # Customer Effort Score (Task 22): a nullable post-resolution
    # micro-survey result, stored once (via `POST /chat/sessions/{id}/survey`)
    # after a session ends. `None` means no survey response has been
    # submitted yet -- the survey is optional/skippable, so this stays `None`
    # for most sessions. No fixed scale is enforced at the model layer (the
    # API layer validates the range -- see `backend/app/api/chat.py`).
    ces_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="chat_sessions")
    device: Mapped["Device"] = relationship("Device", back_populates="chat_sessions")
    support_ticket: Mapped["SupportTicket | None"] = relationship(
        "SupportTicket", back_populates="chat_session", uselist=False, cascade="all, delete-orphan"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="chat_session",
        cascade="all, delete-orphan",
        # `passive_deletes=True` pairs with `ChatMessage.chat_session_id`'s
        # `ondelete="CASCADE"` FK: when a ChatSession is deleted, the ORM
        # trusts the DB's own ON DELETE CASCADE to remove its ChatMessage
        # rows instead of first SELECTing every message to cascade-delete
        # them in Python. This matters for `app.jobs.retention`
        # (`purge_expired_sessions`), which asserts (and depends on, for
        # its no-N+1 scaling claim) that deleting a candidate session issues
        # no extra per-session SELECT -- see that module's test for the
        # exact query-count assertion this preserves.
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ChatSession(chat_session_id={self.chat_session_id!r}, session_status={self.session_status!r})"


class ChatMessage(Base):
    """A single message (the customer's question, or the AI assistant's
    response) within a ChatSession (Task 15).

    Fields per the task brief: role (user/assistant), content,
    chat_session_id (FK), created_at, and a nullable thumbs-style `feedback`
    column -- named exactly `feedback` per a binding pre-flight ruling that a
    later task (22) depends on that exact column name. `feedback` is a
    nullable boolean: `None` means no feedback given yet, `True`/`False` are
    a thumbs-up/thumbs-down rating of the assistant's answer. There is no
    feedback concept for `role=user` rows; the column is simply left `None`
    for those (not worth a CHECK constraint for this POC).
    """

    __tablename__ = "chat_messages"

    chat_message_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.chat_session_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        Enum(
            ChatMessageRole,
            name="chat_message_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    feedback: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    chat_session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ChatMessage(chat_message_id={self.chat_message_id!r}, role={self.role!r})"


class SupportTicket(Base):
    """A human-support escalation, created from at most one ChatSession.

    Fields per the authoritative source spec:
    `SupportTicket: TicketID (PK), CustomerID (FK), DeviceID (FK),
    ChatSessionID (FK), TicketStatus, Priority, CreatedDate, ClosedDate,
    AssignedSupportAgent`.
    """

    __tablename__ = "support_tickets"

    support_ticket_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.chat_session_id"), nullable=False, unique=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.customer_id"), nullable=False
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.device_id"), nullable=False)
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
    device: Mapped["Device"] = relationship("Device", back_populates="support_tickets")
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
