"""`POST /reports/start-of-day` and `POST /reports/end-of-day` (Task 16).

Manually-triggerable endpoints wrapping `app.ai.reports.generate_start_of_day_report`
/ `generate_end_of_day_report`. No scheduler is built for this POC -- the
task brief only requires the functions be *structured* so a cron job could
call them directly, which `app.ai.reports`'s plain `(customer_id, *, db)`
functions already allow (a scheduler could import and call them with no
HTTP layer involved at all). These routes are simply the manual trigger.

RBAC (mirrors `app/api/telematics.py` and `app/api/chat.py`, per the plan's
global constraints): `require_role("customer", "support_agent")`.

`customer_id` scoping, mirroring Task 15's `POST /chat` support_agent
pattern exactly (`app/api/chat.py`'s `_create_new_session`): a
`customer`-role caller is ALWAYS scoped to their own JWT-derived
`current_user.user_id` -- the request body's `customer_id` field is never
honored for that role, closing off any privilege-escalation path via a
client-supplied id. A `support_agent`-role caller has no fleet of their
own, so they MUST supply `customer_id` in the request body (400 if
omitted), same as Task 15's "support_agent must supply customer_id to
start a new chat session" rule.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.reports import generate_end_of_day_report, generate_start_of_day_report
from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db

router = APIRouter(prefix="/reports", tags=["reports"])

_allowed_roles = require_role("customer", "support_agent")


class ReportRequest(BaseModel):
    """`POST /reports/*` request body.

    `customer_id`: only honored for a `support_agent`-role caller (required
    for that role -- support agents have no fleet of their own to default
    to). Ignored for `customer`-role callers, who are always scoped to
    their own JWT-derived customer_id (see module docstring).
    """

    customer_id: int | None = None


class ReportResponse(BaseModel):
    """`POST /reports/*` response body."""

    customer_id: int
    report: str


def _resolve_customer_id(payload: ReportRequest, current_user: CurrentUser) -> int:
    if current_user.role == "customer":
        return current_user.user_id
    if payload.customer_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="customer_id is required for a support_agent to request a report",
        )
    return payload.customer_id


@router.post("/start-of-day", response_model=ReportResponse)
def post_start_of_day_report(
    payload: ReportRequest = ReportRequest(),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> ReportResponse:
    customer_id = _resolve_customer_id(payload, current_user)
    report = generate_start_of_day_report(customer_id, db=db)
    return ReportResponse(customer_id=customer_id, report=report)


@router.post("/end-of-day", response_model=ReportResponse)
def post_end_of_day_report(
    payload: ReportRequest = ReportRequest(),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> ReportResponse:
    customer_id = _resolve_customer_id(payload, current_user)
    report = generate_end_of_day_report(customer_id, db=db)
    return ReportResponse(customer_id=customer_id, report=report)
