"""Audit logging (Task 23): `record_audit_event`, plus the currently-known
`action` name constants.

Scope of what actually gets audited by this task (a deliberate, documented
narrowing of the task brief's "every AI-generated recommendation ... and
every support-agent action (ticket status change)"): this codebase has NO
endpoint anywhere that lets a support agent change a `SupportTicket`'s
status -- Task 20 only added read-only `GET /tickets`. Building a new
ticket-status-update feature just to have something to audit-log would be
scope creep beyond "hardening" into "new feature", so it is not built here.
Instead, this module audits what actually exists and is verifiable: every
AI-generated recommendation shown to a user --

  * `ACTION_CHAT_ANSWER` -- Task 15's `POST /chat`, logged once per turn
    after `handle_answer` resolves the final customer-facing text. Notes
    the answer's confidence score and whether it was escalated.
  * `ACTION_REPORT_GENERATED` -- Task 16's `POST /reports/start-of-day` and
    `POST /reports/end-of-day`, logged once per generated report. Notes
    which report type was generated.

`AuditLog.action` (see `app.models.audit`) is a plain, unconstrained string
specifically so a future ticket-status-change feature (or any other
auditable action) can start writing new action names with no migration --
these two constants are just the currently-known values, not an exhaustive
enum.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

ACTION_CHAT_ANSWER = "chat_answer"
ACTION_REPORT_GENERATED = "report_generated"


def record_audit_event(
    db: Session,
    *,
    actor_id: int | None,
    actor_role: str,
    action: str,
    description: str | None = None,
) -> AuditLog:
    """Insert and commit one `AuditLog` row, mirroring
    `app.repositories.chat`'s add-commit-refresh pattern for a single-purpose
    persistence helper.

    Deliberately swallows nothing: if the insert/commit fails (e.g. a
    dropped DB connection), the exception propagates to the caller rather
    than silently discarding the audit record -- audit logging that can
    fail invisibly defeats its own purpose.
    """
    entry = AuditLog(actor_id=actor_id, actor_role=actor_role, action=action, description=description)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


__all__ = ["ACTION_CHAT_ANSWER", "ACTION_REPORT_GENERATED", "record_audit_event"]
