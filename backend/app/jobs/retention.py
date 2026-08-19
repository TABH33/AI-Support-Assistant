"""Chat session data-retention purge job (Task 9).

Data-privacy/compliance job (POPIA/GDPR-style requirement, per the source
project's own requirements): a `ChatSession` is a log of a customer's
conversation with the AI assistant and must not be retained indefinitely.
Once a session has been sitting past `SESSION_TIMEOUT_MINUTES` since it was
last active, this job deletes it.

Runnable directly as a script (no new scheduler dependency -- an external
scheduler such as cron, a Kubernetes CronJob, or Windows Task Scheduler is
expected to invoke it):

    python -m app.jobs.retention
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.chat import ChatSession
from app.models.enums import TicketStatus

logger = logging.getLogger(__name__)

# A ChatSession that escalated to a SupportTicket only stays exempt from
# purging while the ticket's lifecycle is still active. Once the ticket
# reaches one of these terminal states, it no longer blocks the session
# from being purged under the normal timeout rule -- see the docstring
# below for the full rationale (and its consequence for the ticket itself).
_TERMINAL_TICKET_STATUSES = frozenset({TicketStatus.RESOLVED, TicketStatus.CLOSED})


def purge_expired_sessions(
    db: Session,
    timeout_minutes: int,
    *,
    now: datetime | None = None,
) -> int:
    """Delete `ChatSession` rows whose retention window has elapsed.

    Expiry semantics (per the task brief): a session expires
    `timeout_minutes` after its *reference time* --  `end_time` if the
    session has ended, or `start_time` if it never ended (e.g. abandoned
    mid-conversation, or an ACTIVE session that was simply never
    explicitly closed). A session sitting *exactly* at the boundary --
    elapsed time equal to `timeout_minutes`, to the second -- IS treated
    as expired: retention is framed as "may be kept for up to
    `timeout_minutes`", so reaching that limit already qualifies for
    purge. This is a deliberate `<=` comparison (`reference_time <= cutoff`
    where `cutoff = now - timeout_minutes`), not an accidental `<` vs `<=`
    choice, and is covered explicitly by a boundary test. This comparison
    happens at the SQL level (see the query below), not in Python, so this
    job scales to a cron-scheduled, unattended, indefinitely-running
    workload without loading every `ChatSession` row on every invocation.

    Scope -- deliberately narrow, matching the task brief's "for now, just
    the session row and any directly-owned data". Task 15 later added a
    `ChatMessage` table (`ChatSession.messages`, FK `ON DELETE CASCADE` +
    `passive_deletes=True`); deleting a purged `ChatSession` removes its
    messages via that DB-level cascade, not an ORM-loaded one, so it adds no
    extra SELECT here -- the no-N+1 query-count assertion below still holds.
    The `ChatSession` row itself is deleted once expired,
    **gated on its linked `SupportTicket`'s status, not merely on whether a
    ticket exists**:

    * No `SupportTicket` at all -> purge as soon as expired, same as any
      other session.
    * `SupportTicket` in a non-terminal status (`OPEN` or `IN_PROGRESS`)
      -> the session is NOT purged, even once expired. A ticket still
      being actively worked needs its originating chat context to survive
      alongside it.
    * `SupportTicket` in a terminal status (`RESOLVED` or `CLOSED`) -> the
      session becomes purgeable again under the normal timeout rule, same
      as a session with no ticket at all. Because `ChatSession.support_ticket`
      is declared with `cascade="all, delete-orphan"`, deleting the session
      at that point also deletes the ticket (and, via the ticket's own
      cascade, its `Notification`s). This is intentional, not an oversight:
      gating the exception on ticket existence alone (the job's first
      version) meant *any* session that was ever escalated became exempt
      from timeout-based deletion forever -- the opposite compliance
      failure (indefinite retention) from the one that exception was meant
      to prevent. Narrowing the gate to "only while the ticket's lifecycle
      is still open" fixes that, at the cost of no longer giving resolved/
      closed tickets a separate, longer retention window of their own --
      that's a real trade-off worth knowing about (see the task report),
      but a new "N days after resolution" grace period is explicitly out
      of scope for this job.

    Returns the number of `ChatSession` rows actually deleted.
    """
    reference_now = now if now is not None else datetime.now(timezone.utc)
    cutoff = reference_now - timedelta(minutes=timeout_minutes)

    # SQL-level filter (not a Python-side scan of every row) so this job
    # stays cheap to run on a schedule as the table grows: only rows that
    # are actually expiry candidates are loaded. `joinedload` eager-loads
    # `support_ticket` in that same query, avoiding an N+1 SELECT per
    # candidate when the ticket-status gate below is evaluated.
    candidates = (
        db.query(ChatSession)
        .options(joinedload(ChatSession.support_ticket))
        .filter(
            or_(
                ChatSession.end_time <= cutoff,
                and_(ChatSession.end_time.is_(None), ChatSession.start_time <= cutoff),
            )
        )
        .all()
    )

    purged = 0
    for chat_session in candidates:
        ticket = chat_session.support_ticket
        if ticket is not None and ticket.ticket_status not in _TERMINAL_TICKET_STATUSES:
            # Expired, but the linked ticket is still actively being
            # worked -- leave both the session and the ticket alone.
            logger.info(
                "Skipping expired ChatSession %s: linked SupportTicket %s is still %s.",
                chat_session.chat_session_id,
                ticket.support_ticket_id,
                ticket.ticket_status.value,
            )
            continue

        db.delete(chat_session)
        purged += 1

    if purged:
        db.commit()

    logger.info("Session retention: purged %d expired ChatSession row(s).", purged)
    return purged


def main() -> None:
    """Entry point for `python -m app.jobs.retention`.

    Uses `settings.session_timeout_minutes` as the timeout, imported lazily
    inside `main()` (rather than at module scope) purely so this module can
    be imported by tests without requiring a live database connection --
    building `SessionLocal` still only requires `settings.database_url` to
    be a valid URL string, not a reachable database, but keeping the import
    scoped here keeps the module's import-time footprint minimal.
    """
    from app.database import SessionLocal

    logging.basicConfig(level=logging.INFO)
    db = SessionLocal()
    try:
        purged = purge_expired_sessions(db, settings.session_timeout_minutes)
        print(f"Purged {purged} expired chat session(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
