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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.chat_service import answer_query
from app.ai.escalation import handle_answer
from app.ai.retrieval import retrieve_context
from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.device import Device
from app.models.enums import ChatMessageRole
from app.repositories.chat import create_chat_session

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
    """`POST /chat` response body."""

    session_id: int
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

    db.add(
        ChatMessage(
            chat_session_id=chat_session.chat_session_id,
            role=ChatMessageRole.USER,
            content=payload.query,
        )
    )
    db.add(
        ChatMessage(
            chat_session_id=chat_session.chat_session_id,
            role=ChatMessageRole.ASSISTANT,
            content=escalation_result.text,
        )
    )
    db.commit()

    return ChatResponse(
        session_id=chat_session.chat_session_id,
        answer=escalation_result.text,
        confidence=chat_answer.confidence,
        escalated=escalation_result.escalated,
    )
