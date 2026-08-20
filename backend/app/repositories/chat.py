"""Data-access functions for ChatSession, SupportTicket, and Notification
(Task 8; mirrors Task 4's `app.models.chat` schema).

Pure data-access: each function accepts a SQLAlchemy `Session` plus the
fields needed to build/update a row, performs the insert/update, and returns
the persisted ORM object. No AI/RAG logic lives here -- deciding *when* to
end a session, escalate to a ticket, or send a notification is Task 12-14's
job; this module only knows how to persist the result of those decisions.

Transaction boundary (final-review Fix 6): these functions `db.flush()`
rather than `db.commit()`. They participate in whatever transaction the
CALLER already has open rather than each silently owning (and closing) its
own -- `db.flush()` still sends the INSERT/UPDATE to the database and
assigns server-generated values (autoincrement PKs, defaults), which is all
`db.refresh()` below needs to reload them, but it does NOT make the change
durable or visible to other transactions/connections. The caller (e.g.
`app.api.chat`'s `POST /chat` route) is responsible for the actual
`db.commit()`, once, after everything for that request has been staged --
this is what makes a whole request atomic: if anything later in the same
request fails, everything flushed-but-not-committed here rolls back with it,
instead of leaving e.g. a `SupportTicket` committed with no `ChatMessage`
rows to back it (the bug this fix closes; see `app/api/chat.py`'s module
docstring).

Cardinality enforced at the DB level (Task 4) and exercised by this module's
tests, not re-implemented here:
  * ChatSession 1 -> 0..1 SupportTicket, via a UNIQUE constraint on
    `SupportTicket.chat_session_id`. A second `create_support_ticket()` call
    for the same `chat_session_id` will raise `sqlalchemy.exc.IntegrityError`
    on flush -- callers that want a friendlier error should catch that
    themselves (see `app.ai.escalation._get_or_create_escalation_ticket` and
    `app.api.chat._get_or_create_feedback_escalation_ticket`); this layer
    does not swallow or translate it.
  * SupportTicket 1 -> Many Notification, via `Notification.support_ticket_id`
    (no uniqueness constraint -- a ticket can have any number of
    notifications).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.chat import ChatSession, Notification, SupportTicket
from app.models.enums import PreferredNotificationMethod, Priority, SessionStatus, TicketStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_chat_session(
    db: Session,
    customer_id: int,
    device_id: int,
    *,
    session_status: SessionStatus = SessionStatus.ACTIVE,
    start_time: datetime | None = None,
    ai_confidence_score: float | None = None,
) -> ChatSession:
    """Insert a new ChatSession for `customer_id` in the context of `device_id`.

    `start_time` defaults to now (UTC) if not supplied; `session_status`
    defaults to ACTIVE, matching the model's own column default.

    Flushes, does not commit (see module docstring) -- the caller owns the
    transaction boundary.
    """
    chat_session = ChatSession(
        customer_id=customer_id,
        device_id=device_id,
        session_status=session_status,
        start_time=start_time if start_time is not None else _now(),
        ai_confidence_score=ai_confidence_score,
    )
    db.add(chat_session)
    db.flush()
    db.refresh(chat_session)
    return chat_session


def end_chat_session(
    db: Session,
    chat_session_id: int,
    *,
    end_time: datetime | None = None,
    session_status: SessionStatus = SessionStatus.ENDED,
    ai_confidence_score: float | None = None,
) -> ChatSession:
    """Mark an existing ChatSession as ended: sets `end_time` (default now,
    UTC) and `session_status` (default ENDED). If `ai_confidence_score` is
    given, it overwrites the session's stored score (e.g. once the AI's
    final confidence for the whole conversation is known); if omitted, the
    existing value is left untouched.

    Raises `ValueError` if no ChatSession with `chat_session_id` exists.

    Flushes, does not commit (see module docstring) -- the caller owns the
    transaction boundary.
    """
    chat_session = db.get(ChatSession, chat_session_id)
    if chat_session is None:
        raise ValueError(f"ChatSession {chat_session_id!r} not found")

    chat_session.end_time = end_time if end_time is not None else _now()
    chat_session.session_status = session_status
    if ai_confidence_score is not None:
        chat_session.ai_confidence_score = ai_confidence_score

    db.flush()
    db.refresh(chat_session)
    return chat_session


def create_support_ticket(
    db: Session,
    chat_session_id: int,
    customer_id: int,
    device_id: int,
    *,
    assigned_support_agent_id: int | None = None,
    ticket_status: TicketStatus = TicketStatus.OPEN,
    priority: Priority = Priority.MEDIUM,
    subject: str | None = None,
    description: str | None = None,
) -> SupportTicket:
    """Insert a new SupportTicket escalated from `chat_session_id`.

    `chat_session_id` is unique on `SupportTicket` (Task 4's schema), so a
    second ticket for the same chat session raises `IntegrityError` on
    flush -- that's the DB enforcing ChatSession 1 -> 0..1 SupportTicket;
    this function does not pre-check for an existing ticket itself.

    Flushes, does not commit (see module docstring) -- the caller owns the
    transaction boundary.
    """
    ticket = SupportTicket(
        chat_session_id=chat_session_id,
        customer_id=customer_id,
        device_id=device_id,
        assigned_support_agent_id=assigned_support_agent_id,
        ticket_status=ticket_status,
        priority=priority,
        subject=subject,
        description=description,
    )
    db.add(ticket)
    db.flush()
    db.refresh(ticket)
    return ticket


def create_notification(
    db: Session,
    support_ticket_id: int,
    customer_id: int,
    notification_type: PreferredNotificationMethod,
    message: str,
    *,
    sent_at: datetime | None = None,
) -> Notification:
    """Insert a new Notification for `support_ticket_id`, addressed to
    `customer_id` via `notification_type`. `sent_at` is left `None` by
    default (queued/not-yet-sent); pass it once delivery is confirmed, or
    to record delivery time directly at creation.

    Flushes, does not commit (see module docstring) -- the caller owns the
    transaction boundary.
    """
    notification = Notification(
        support_ticket_id=support_ticket_id,
        customer_id=customer_id,
        notification_type=notification_type,
        message=message,
        sent_at=sent_at,
    )
    db.add(notification)
    db.flush()
    db.refresh(notification)
    return notification
