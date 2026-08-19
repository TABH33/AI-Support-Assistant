"""Escalation & ticketing logic (Task 14): `handle_answer`.

Bridges Task 13's LLM chat service (`ChatAnswer`) and Task 8's chat
repository (`create_support_ticket`, `create_notification`): decides
whether a given `ChatAnswer` is confident enough to show to the customer
as-is, or whether it must instead be escalated to a human support agent.

Escalation rule (per the task brief): when `chat_answer.confidence` is
strictly below `settings.escalation_confidence_threshold`, the raw LLM
answer is considered unreliable enough that it must not be shown to the
customer. In that case this module:

  1. Creates a `SupportTicket` (Task 8's `create_support_ticket`) linked to
     the given `chat_session_id`, so a human agent can pick it up.
  2. Creates a `Notification` (Task 8's `create_notification`) informing the
     customer their query has been escalated.
  3. Returns Task 13's exact `FALLBACK_TEXT` constant -- never the raw LLM
     text -- so the customer-facing message matches ASS2's compliance
     requirement verbatim (see `chat_service.py`'s module docstring).

At or above the threshold, the `ChatAnswer.text` is returned unchanged and
no ticket/notification is created.

`customer_id`/`device_id` for `create_support_ticket` are looked up from the
`ChatSession` row identified by `chat_session_id`, rather than accepted as
separate parameters -- `handle_answer`'s caller (the chat endpoint handling
one turn of an existing session) already has `chat_session_id` in hand and
should not need to separately track/pass the session's `customer_id`/
`device_id` just to escalate; those fields live on `ChatSession` and Task 8's
repository has no lookup-by-id helper of its own, so this module reads the
row directly via `db.get(ChatSession, ...)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer
from app.config import settings
from app.models.chat import ChatSession
from app.models.enums import PreferredNotificationMethod
from app.repositories.chat import create_notification, create_support_ticket

#: Message stored on the `Notification` created when an answer is escalated.
_ESCALATION_NOTIFICATION_MESSAGE = (
    "Your question has been escalated to a support agent because the AI "
    "assistant could not confidently answer it. A support ticket has been "
    "created and an agent will follow up with you."
)


@dataclass
class EscalationResult:
    """The result of one `handle_answer` call.

    `text` is what should actually be shown to the customer: either the
    original `ChatAnswer.text` (not escalated) or Task 13's `FALLBACK_TEXT`
    (escalated). `escalated` records which case applied, and
    `support_ticket_id` is the id of the newly created `SupportTicket` when
    escalated, or `None` when not.
    """

    text: str
    escalated: bool
    support_ticket_id: int | None


def handle_answer(
    db: Session,
    chat_session_id: int,
    chat_answer: ChatAnswer,
    *,
    notification_type: PreferredNotificationMethod = PreferredNotificationMethod.IN_APP,
) -> EscalationResult:
    """Decide whether `chat_answer` needs escalation, act on that decision,
    and return the customer-facing `EscalationResult`.

    Below `settings.escalation_confidence_threshold`: looks up the
    `ChatSession` for `chat_session_id` (raises `ValueError` if it doesn't
    exist -- mirrors `end_chat_session`'s own not-found handling in Task 8's
    repository) to get `customer_id`/`device_id`, creates a `SupportTicket`
    and a `Notification` via Task 8's repository functions, and returns
    `FALLBACK_TEXT` with `escalated=True`.

    At or above the threshold: returns `chat_answer.text` unchanged, with
    `escalated=False` and `support_ticket_id=None`. No ticket or
    notification is created.
    """
    if chat_answer.confidence >= settings.escalation_confidence_threshold:
        return EscalationResult(text=chat_answer.text, escalated=False, support_ticket_id=None)

    chat_session = db.get(ChatSession, chat_session_id)
    if chat_session is None:
        raise ValueError(f"ChatSession {chat_session_id!r} not found")

    ticket = create_support_ticket(
        db,
        chat_session_id=chat_session_id,
        customer_id=chat_session.customer_id,
        device_id=chat_session.device_id,
        subject="AI assistant could not confidently answer a customer question",
        description=chat_answer.text,
    )
    create_notification(
        db,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=chat_session.customer_id,
        notification_type=notification_type,
        message=_ESCALATION_NOTIFICATION_MESSAGE,
    )

    return EscalationResult(
        text=FALLBACK_TEXT, escalated=True, support_ticket_id=ticket.support_ticket_id
    )
