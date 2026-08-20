"""`POST /chat` -- the customer/support-agent-facing chat endpoint (Task 15).

Wires together the full AI pipeline built in Tasks 12-14 behind one HTTP
route: Task 12's `retrieve_context` -> Task 13's `answer_query` -> Task 14's
`handle_answer`, persisting the turn as two `ChatMessage` rows (Task 15's
own model addition) and returning the customer-facing answer.

RBAC (per the plan's global constraints, mirroring `app/api/telematics.py`):
`require_role("customer", "support_agent")`.

**Security requirement this endpoint exists to get right** (same class of
issue Tasks 7/10/12 all had to handle): `driver_id`/`trip_id`/`vehicle_id`
in the request body are CLIENT-SUPPLIED and never trusted blindly. The
`customer_id` passed to `retrieve_context` is always derived from the
resolved `ChatSession` -- never from a client-supplied `customer_id` field
directly -- so a customer-role caller can never get another customer's
telematics data reflected into their answer, regardless of what
driver_id/trip_id/vehicle_id they pass:

  * Reusing an existing session: the session must already belong to the
    caller (customer role) or is used as-is (support_agent, unrestricted
    like Task 7's `_scoped_*` helpers) -- either way, `customer_id` for
    retrieval comes from `chat_session.customer_id`, the session's actual
    owner, not anything in this request.
  * Creating a new session: for a `customer`-role caller, the new session's
    `customer_id` is always `current_user.user_id` (the JWT-derived id) --
    the request's optional `customer_id` field is only ever honored for
    `support_agent`-role callers (mirrors Task 7's `/devices?customer_id=`
    pattern, since support agents have no fleet of their own to default to).
    Creating a session also requires `device_id`, validated to belong to
    that resolved `customer_id` (404 if it doesn't -- same "don't leak
    existence" posture as Task 7's cross-tenant 404s).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Query as SAQuery, Session

from app.ai.chat_service import answer_query
from app.ai.escalation import handle_answer
from app.ai.retrieval import retrieve_context
from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db
from app.models.chat import ChatMessage, ChatSession, Notification, SupportTicket
from app.models.device import Device
from app.models.enums import ChatMessageRole, PreferredNotificationMethod, Priority, TicketStatus
from app.repositories.chat import create_chat_session, create_notification, create_support_ticket

router = APIRouter(tags=["chat"])

_allowed_roles = require_role("customer", "support_agent")


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """`POST /chat` request body.

    `session_id`: reuse an existing `ChatSession` if given; a new one is
    created if absent (in which case `device_id` -- and, for a
    `support_agent` caller, `customer_id` -- are required; see module
    docstring).

    `driver_id`/`trip_id`/`vehicle_id`: client-supplied ids used to resolve
    telematics context via `retrieve_context` -- NEVER trusted for tenant
    scoping on their own (see module docstring's security note).

    `customer_id`: only honored when a `support_agent`-role caller is
    starting a new session (support agents have no fleet of their own to
    default to, mirroring Task 7's `/devices?customer_id=` pattern).
    Ignored for `customer`-role callers and when reusing an existing
    session (the session's own `customer_id` always wins in that case).
    """

    session_id: int | None = None
    query: str
    driver_id: int | None = None
    trip_id: int | None = None
    vehicle_id: int | None = None
    device_id: int | None = None
    customer_id: int | None = None


class ChatResponse(BaseModel):
    """`POST /chat` response body.

    `message_id`: the persisted `ChatMessage.chat_message_id` of the
    assistant's turn (added in Task 22) -- the frontend needs this to submit
    thumbs up/down feedback against the exact message via
    `PATCH /chat/messages/{message_id}/feedback`, since nothing else in this
    response identifies which `ChatMessage` row the answer became.
    """

    session_id: int
    message_id: int
    answer: str
    confidence: float
    escalated: bool


# ---------------------------------------------------------------------------
# Session resolution helpers
# ---------------------------------------------------------------------------


def _resolve_existing_session(
    db: Session, session_id: int, current_user: CurrentUser
) -> ChatSession:
    """Load `session_id`, enforcing that a `customer`-role caller can only
    reuse a session that belongs to them. A `support_agent`-role caller may
    reuse any session (unrestricted across customers, same as Task 7's
    `support_agent`-is-unrestricted rule).

    A session that doesn't exist, or (for a customer caller) exists but
    belongs to a different customer, is reported identically as 404 -- a
    customer must not be able to distinguish "no such session" from "exists
    but isn't yours" (mirrors Task 7's cross-tenant 404 posture)."""
    chat_session = db.get(ChatSession, session_id)
    if chat_session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    if current_user.role == "customer" and chat_session.customer_id != current_user.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return chat_session


def _create_new_session(
    db: Session, payload: ChatRequest, current_user: CurrentUser
) -> ChatSession:
    """Create a new `ChatSession` for this turn.

    `customer_id` is the JWT-derived `current_user.user_id` for a
    `customer`-role caller -- the request body's `customer_id` field is
    never honored for that role. For a `support_agent`-role caller,
    `payload.customer_id` is required (agents have no fleet of their own).

    `device_id` is always required to start a new session, and is validated
    to belong to the resolved `customer_id` (404, not 400, if it doesn't --
    same "don't leak existence" posture as the rest of this module)."""
    if current_user.role == "customer":
        customer_id = current_user.user_id
    else:
        if payload.customer_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="customer_id is required for a support_agent to start a new chat session",
            )
        customer_id = payload.customer_id

    if payload.device_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="device_id is required to start a new chat session",
        )

    device = (
        db.query(Device)
        .filter(Device.device_id == payload.device_id, Device.customer_id == customer_id)
        .one_or_none()
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Device not found")

    return create_chat_session(db, customer_id=customer_id, device_id=payload.device_id)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
def post_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> ChatResponse:
    if payload.session_id is not None:
        chat_session = _resolve_existing_session(db, payload.session_id, current_user)
    else:
        chat_session = _create_new_session(db, payload, current_user)

    # Always derive customer_id for retrieval from the resolved session --
    # never from the request body directly -- so tenant scoping cannot be
    # bypassed by a mismatched/forged customer_id in the request (see module
    # docstring's security note).
    customer_id = chat_session.customer_id

    retrieved_context = retrieve_context(
        payload.query,
        driver_id=payload.driver_id,
        trip_id=payload.trip_id,
        vehicle_id=payload.vehicle_id,
        customer_id=customer_id,
        db=db,
    )
    chat_answer = answer_query(payload.query, retrieved_context)
    escalation_result = handle_answer(db, chat_session.chat_session_id, chat_answer)

    assistant_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content=escalation_result.text,
    )
    db.add(
        ChatMessage(
            chat_session_id=chat_session.chat_session_id,
            role=ChatMessageRole.USER,
            content=payload.query,
        )
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    return ChatResponse(
        session_id=chat_session.chat_session_id,
        message_id=assistant_message.chat_message_id,
        answer=escalation_result.text,
        confidence=chat_answer.confidence,
        escalated=escalation_result.escalated,
    )


# ---------------------------------------------------------------------------
# `GET /tickets`, `GET /notifications` -- Task 20's read endpoints for the
# Alerts screen.
#
# RBAC/scoping mirrors `app/api/telematics.py`'s `_scoped_*` pattern exactly
# (see that module's docstring): `require_role("customer", "support_agent")`,
# a `customer`-role caller is always filtered to `SupportTicket.customer_id
# == current_user.user_id` / `Notification.customer_id ==
# current_user.user_id` (the JWT-derived id -- never a client-supplied
# `customer_id`), and a `support_agent`-role caller sees every customer's
# tickets/notifications, optionally narrowed by an explicit `?customer_id=`
# query filter (mirrors Task 7's `/devices?customer_id=`).
# ---------------------------------------------------------------------------


class SupportTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    support_ticket_id: int
    chat_session_id: int
    customer_id: int
    device_id: int
    assigned_support_agent_id: int | None
    ticket_status: TicketStatus
    priority: Priority
    subject: str | None
    description: str | None
    created_at: datetime
    resolved_at: datetime | None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notification_id: int
    support_ticket_id: int
    customer_id: int
    notification_type: PreferredNotificationMethod
    message: str
    sent_at: datetime | None
    created_at: datetime


def _scoped_tickets(db: Session, current_user: CurrentUser) -> SAQuery:
    query = db.query(SupportTicket)
    if current_user.role == "customer":
        query = query.filter(SupportTicket.customer_id == current_user.user_id)
    return query


def _scoped_notifications(db: Session, current_user: CurrentUser) -> SAQuery:
    query = db.query(Notification)
    if current_user.role == "customer":
        query = query.filter(Notification.customer_id == current_user.user_id)
    return query


@router.get("/tickets", response_model=list[SupportTicketOut])
def list_tickets(
    customer_id: int | None = Query(
        default=None,
        description=(
            "Filter by customer. Ignored for `customer`-role callers (who "
            "are always scoped to their own customer_id); optional for "
            "`support_agent` callers, who see all customers' tickets when "
            "omitted."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[SupportTicket]:
    query = _scoped_tickets(db, current_user)
    if current_user.role == "support_agent" and customer_id is not None:
        query = query.filter(SupportTicket.customer_id == customer_id)
    return query.order_by(SupportTicket.support_ticket_id).all()


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    customer_id: int | None = Query(
        default=None,
        description=(
            "Filter by customer. Ignored for `customer`-role callers (who "
            "are always scoped to their own customer_id); optional for "
            "`support_agent` callers, who see all customers' notifications "
            "when omitted."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[Notification]:
    query = _scoped_notifications(db, current_user)
    if current_user.role == "support_agent" and customer_id is not None:
        query = query.filter(Notification.customer_id == customer_id)
    return query.order_by(Notification.notification_id).all()


# ---------------------------------------------------------------------------
# `PATCH /chat/messages/{id}/feedback`, `POST /chat/sessions/{id}/survey` --
# Task 22's feedback loop UI: thumbs up/down on an assistant `ChatMessage`,
# and a post-resolution Customer Effort Score (CES) micro-survey per
# `ChatSession`.
#
# RBAC/scoping mirrors the rest of this module: `require_role("customer",
# "support_agent")`, and a `customer`-role caller can only act on a message
# whose `ChatSession.customer_id` (feedback) / a session (survey) is their
# own -- identical "don't leak existence, 404 either way" posture as
# `_resolve_existing_session` above. `support_agent` is unrestricted, same as
# every other route in this file.
#
# Thumbs-down idempotency (the brief's core requirement): `ChatSession 1 ->
# 0..1 SupportTicket` is a DB-level UNIQUE constraint on
# `SupportTicket.chat_session_id` (Task 4's schema; see Task 8's
# `create_support_ticket` docstring). Task 14's `handle_answer` never has to
# worry about a duplicate because a session is only auto-escalated once (at
# most one low-confidence answer triggers it, and nothing re-runs
# `handle_answer` for an already-escalated session). Feedback is different:
# a customer can press thumbs-down on the same message repeatedly (double
# click, retry after a flaky network response, etc.), and a session's
# assistant answer might ALREADY have been auto-escalated by Task 14 before
# the customer ever presses thumbs-down. Both cases must be a no-op, not an
# `IntegrityError` -- so `_get_or_create_feedback_escalation_ticket` always
# SELECTs for an existing ticket first (never blindly calls
# `create_support_ticket` and catches the exception).
# ---------------------------------------------------------------------------


class ChatMessageFeedbackRequest(BaseModel):
    """`PATCH /chat/messages/{id}/feedback` request body."""

    feedback: bool


class ChatMessageFeedbackResponse(BaseModel):
    """`PATCH /chat/messages/{id}/feedback` response body.

    `escalated`/`support_ticket_id` report whether this session now has a
    (new-or-pre-existing) `SupportTicket` as a result of this call -- always
    populated together with `feedback=False`, always `False`/`None` for
    `feedback=True` (a thumbs-up never escalates).
    """

    chat_message_id: int
    feedback: bool
    escalated: bool
    support_ticket_id: int | None


#: Message stored on the `Notification` created when a thumbs-down triggers
#: an escalation ticket -- mirrors `app.ai.escalation._ESCALATION_NOTIFICATION_MESSAGE`'s
#: wording/purpose for the low-confidence auto-escalation path.
_FEEDBACK_ESCALATION_NOTIFICATION_MESSAGE = (
    "Your negative feedback on an AI assistant response has been escalated "
    "to a support agent. A support ticket has been created and an agent "
    "will follow up with you."
)


def _get_or_create_feedback_escalation_ticket(
    db: Session, chat_session: ChatSession, message: ChatMessage
) -> SupportTicket:
    """Idempotently escalate a thumbs-down assistant response to a human
    support ticket, reusing Task 8's `create_support_ticket`/
    `create_notification` (the same repository functions Task 14's
    `handle_answer` uses for the low-confidence auto-escalation path).

    `ChatSession 1 -> 0..1 SupportTicket` (Task 4's UNIQUE constraint on
    `SupportTicket.chat_session_id`) means at most one ticket can ever exist
    per session. This SELECTs for an existing ticket for
    `chat_session.chat_session_id` FIRST -- rather than calling
    `create_support_ticket` and catching the `IntegrityError` it would raise
    on a duplicate insert -- so pressing thumbs-down twice (or thumbs-down
    on a session Task 14 already auto-escalated for low confidence) reuses
    the existing ticket instead of erroring.
    """
    existing = (
        db.query(SupportTicket)
        .filter(SupportTicket.chat_session_id == chat_session.chat_session_id)
        .one_or_none()
    )
    if existing is not None:
        return existing

    ticket = create_support_ticket(
        db,
        chat_session_id=chat_session.chat_session_id,
        customer_id=chat_session.customer_id,
        device_id=chat_session.device_id,
        subject="Customer gave thumbs-down feedback on an AI assistant response",
        description=message.content,
    )
    create_notification(
        db,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=chat_session.customer_id,
        notification_type=PreferredNotificationMethod.IN_APP,
        message=_FEEDBACK_ESCALATION_NOTIFICATION_MESSAGE,
    )
    return ticket


@router.patch("/chat/messages/{chat_message_id}/feedback", response_model=ChatMessageFeedbackResponse)
def submit_message_feedback(
    chat_message_id: int,
    payload: ChatMessageFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> ChatMessageFeedbackResponse:
    message = db.get(ChatMessage, chat_message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat message not found")

    chat_session = db.get(ChatSession, message.chat_session_id)
    if chat_session is None or (
        current_user.role == "customer" and chat_session.customer_id != current_user.user_id
    ):
        # Identical 404 for "no such message" and "exists but isn't yours" --
        # same cross-tenant posture as `_resolve_existing_session` above.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat message not found")

    if message.role != ChatMessageRole.ASSISTANT:
        # Per `ChatMessage.feedback`'s own docstring (Task 15): there is no
        # feedback concept for a `role=user` row.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Feedback can only be given on an assistant message",
        )

    message.feedback = payload.feedback
    db.commit()
    db.refresh(message)

    support_ticket_id: int | None = None
    if payload.feedback is False:
        ticket = _get_or_create_feedback_escalation_ticket(db, chat_session, message)
        support_ticket_id = ticket.support_ticket_id

    return ChatMessageFeedbackResponse(
        chat_message_id=message.chat_message_id,
        feedback=message.feedback,
        escalated=support_ticket_id is not None,
        support_ticket_id=support_ticket_id,
    )


class CesSurveyRequest(BaseModel):
    """`POST /chat/sessions/{id}/survey` request body.

    `score`: a Customer Effort Score on the standard 1 (very easy) - 7 (very
    difficult) CES-7 scale. Range is validated here (422 outside 1-7) --
    there is no CHECK constraint at the DB layer (POC-level rigor, matching
    `ChatMessage.feedback`'s own docstring on not adding a CHECK constraint
    for a similarly-scoped rule).
    """

    score: int = Field(ge=1, le=7)


class CesSurveyResponse(BaseModel):
    """`POST /chat/sessions/{id}/survey` response body."""

    chat_session_id: int
    ces_score: int


@router.post("/chat/sessions/{chat_session_id}/survey", response_model=CesSurveyResponse)
def submit_ces_survey(
    chat_session_id: int,
    payload: CesSurveyRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> CesSurveyResponse:
    # Reuses `_resolve_existing_session`'s exact ownership check: a
    # `customer`-role caller can only survey a session that belongs to them
    # (identical 404-either-way posture), `support_agent` is unrestricted.
    chat_session = _resolve_existing_session(db, chat_session_id, current_user)

    chat_session.ces_score = payload.score
    db.commit()
    db.refresh(chat_session)

    return CesSurveyResponse(
        chat_session_id=chat_session.chat_session_id,
        ces_score=chat_session.ces_score,
    )
