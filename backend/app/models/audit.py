"""AuditLog model (Task 23).

Records every AI-generated recommendation shown to a user: a chat answer
(Task 15's `POST /chat`) and a generated report (Task 16's
`POST /reports/start-of-day` / `POST /reports/end-of-day`). See
`app.security.audit` for the function that actually writes rows, and that
module's docstring for why a support-agent ticket-status-change action is
NOT wired up yet (no such endpoint exists in this codebase -- Task 20 only
added read-only `GET /tickets`; see Task 23's report for the scoping call).

`action` is a plain, unconstrained string (not a DB-level enum) precisely so
a future ticket-status-change feature -- or any other auditable action --
can start writing new `action` values without a schema migration. See
`app.security.audit` for the current set of known action-name constants.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """One audited event: who (actor_id/actor_role) did what (action), when
    (created_at), with an optional short human-readable note (description).

    `actor_id` is nullable to allow for a future system-initiated action
    (e.g. an unattended scheduled report run) that has no human actor --
    every actor recorded by this task's own call sites (Task 15/16's
    endpoints) always has a real JWT-derived id, so it is populated in
    practice today.
    """

    __tablename__ = "audit_logs"

    audit_log_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "customer" / "support_agent" today (mirrors `app.auth.security.Role`),
    # left as a plain string rather than importing that Literal/an Enum so
    # a future non-request actor (e.g. "system") doesn't need a schema
    # change to be recorded.
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    # e.g. "chat_answer", "report_generated" -- see `app.security.audit` for
    # the current constants. Deliberately a plain String, not a DB Enum, so
    # new action types (e.g. a future "ticket_status_change") never require
    # a migration to add.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AuditLog(audit_log_id={self.audit_log_id!r}, action={self.action!r})"
