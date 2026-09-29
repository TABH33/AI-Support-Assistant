"""Escalation logic (Task 14, reworked for opt-in escalation): `handle_answer`
and `get_or_create_escalation_ticket`.

Opt-in, not automatic (docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).
This module used to create a `SupportTicket` + `Notification` silently
whenever an answer's confidence fell below the threshold. It no longer does.
There are now two separate steps with two separate callers:

  1. `handle_answer` -- called by `POST /chat` for every RAG turn -- only
     DECIDES. Below `settings.escalation_confidence_threshold` it swaps the
     raw LLM text for Task 13's exact `FALLBACK_TEXT` (the ASS2 compliance
     requirement: an unreliable answer is never shown to the customer; see
     `chat_service.py`'s module docstring) and sets
     `escalation_offered=True`, so the chat widget can ask "Would you like
     to escalate this to a human?". It touches no database row at all --
     which is why it no longer takes a `Session`.
  2. `get_or_create_escalation_ticket` -- called only by
     `POST /chat/messages/{id}/escalate`, i.e. only after the customer
     explicitly said yes -- creates the ticket + in-app notification,
     idempotently.

`EscalationResult.escalated` keeps its meaning ("a ticket now exists for
this exchange"). Since `handle_answer` never creates one, it now always
returns `escalated=False`; nothing implicit ever sets it again.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer
from app.config import settings
from app.models.chat import ChatSession, SupportTicket
from app.models.enums import PreferredNotificationMethod
from app.repositories.chat import create_notification, create_support_ticket

_ESCALATION_TICKET_SUBJECT = (
    "Customer asked for a human after the AI assistant could not confidently answer"
)

#: Message stored on the in-app `Notification` created when a customer
#: accepts the escalation offer.
_ESCALATION_NOTIFICATION_MESSAGE = (
    "You asked for your question to be escalated to a support agent after "
    "the AI assistant could not confidently answer it. A support ticket has "
    "been created and an agent will follow up with you."
)


@dataclass
class EscalationResult:
    """The result of one `handle_answer` call.

    `text` is what should actually be shown to the customer: either the
    original `ChatAnswer.text` (confident) or Task 13's `FALLBACK_TEXT`
    (not confident). `escalation_offered` is True exactly when the fallback
    was used -- the customer should be asked whether to escalate.
    `escalated`/`support_ticket_id` describe a ticket that exists for this
    exchange; `handle_answer` never creates one, so they are always
    `False`/`None` here (the explicit-confirm endpoint reports its own).
    """

    text: str
    escalated: bool
    escalation_offered: bool
    support_ticket_id: int | None


def get_or_create_escalation_ticket(
    db: Session,
    chat_session: ChatSession,
    *,
    description: str,
    notification_type: PreferredNotificationMethod = PreferredNotificationMethod.IN_APP,
) -> SupportTicket:
    """Idempotently create (or reuse) this session's escalation ticket,
    mirroring `app.api.chat._get_or_create_feedback_escalation_ticket`'s
    select-first pattern for the same `ChatSession 1 -> 0..1 SupportTicket`
    UNIQUE constraint (`SupportTicket.chat_session_id`, Task 4's schema).

    Called by `POST /chat/messages/{id}/escalate` after the customer says
    yes. A double-click on "Yes", a later "Yes" on another answer in the
    same session, or a session that already has a thumbs-down ticket must
    all reuse the one existing ticket -- so this SELECTs for it FIRST.

    For the theoretical concurrent case -- two simultaneous requests for the
    same session racing each other -- the initial SELECT can still miss a
    not-yet-committed insert from the other request, so the insert itself
    is also wrapped in `try/except IntegrityError` with a re-select
    fallback. Only a genuinely new ticket gets a new `Notification`; a
    reused ticket does not send a duplicate one.

    The insert runs inside a `db.begin_nested()` SAVEPOINT, not a plain
    `try/except` (final-review Fix 6): the caller owns one request-wide
    transaction, so a bare `db.rollback()` here would discard everything
    else that request had staged, not just the failed insert.

    Flushes, never commits -- the caller owns `db.commit()`.
    """
    existing = (
        db.query(SupportTicket)
        .filter(SupportTicket.chat_session_id == chat_session.chat_session_id)
        .one_or_none()
    )
    if existing is not None:
        return existing

    try:
        with db.begin_nested():
            ticket = create_support_ticket(
                db,
                chat_session_id=chat_session.chat_session_id,
                customer_id=chat_session.customer_id,
                device_id=chat_session.device_id,
                subject=_ESCALATION_TICKET_SUBJECT,
                description=description,
            )
    except IntegrityError:
        existing = (
            db.query(SupportTicket)
            .filter(SupportTicket.chat_session_id == chat_session.chat_session_id)
            .one_or_none()
        )
        if existing is None:
            # The IntegrityError wasn't from a concurrent ticket for this
            # session after all -- re-raise rather than swallow an
            # unrelated failure.
            raise
        return existing

    create_notification(
        db,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=chat_session.customer_id,
        notification_type=notification_type,
        message=_ESCALATION_NOTIFICATION_MESSAGE,
    )
    return ticket


def handle_answer(chat_answer: ChatAnswer) -> EscalationResult:
    """Decide whether `chat_answer` is confident enough to show as-is.

    At or above `settings.escalation_confidence_threshold`: returns
    `chat_answer.text` unchanged, `escalation_offered=False`.

    Below it: returns the exact `FALLBACK_TEXT` constant (never the raw LLM
    text) with `escalation_offered=True`. No ticket or notification is
    created -- that only happens if the customer accepts the offer, via
    `POST /chat/messages/{id}/escalate` -> `get_or_create_escalation_ticket`.
    """
    if chat_answer.confidence >= settings.escalation_confidence_threshold:
        return EscalationResult(
            text=chat_answer.text,
            escalated=False,
            escalation_offered=False,
            support_ticket_id=None,
        )
    return EscalationResult(
        text=FALLBACK_TEXT,
        escalated=False,
        escalation_offered=True,
        support_ticket_id=None,
    )
