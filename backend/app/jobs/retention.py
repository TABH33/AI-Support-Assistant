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

from sqlalchemy.orm import Session

from app.config import settings
from app.models.chat import ChatSession

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC.

    `ChatSession.start_time`/`end_time` are always written as UTC-aware
    values (see `app.repositories.chat._now()`), but on SQLite -- used by
    this repo's test suite, since there is no native timezone-aware
    datetime type there -- a value can come back from the DB with its
    tzinfo stripped even though the column is declared
    `DateTime(timezone=True)`. A naive value is therefore assumed to
    already be UTC rather than compared directly against a tz-aware `now`
    (which would raise `TypeError`) or silently misinterpreted as local
    time.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


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
    choice, and is covered explicitly by a boundary test.

    Scope -- deliberately narrow, matching the task brief's "for now, just
    the session row and any directly-owned data" (Task 15 later adds a
    `ChatMessage` table; no such table exists yet, so there is nothing else
    to purge there). Only the `ChatSession` row itself is deleted.
    `ChatSession.support_ticket` is NOT deleted as a side effect, even
    though the ORM relationship is declared with
    `cascade="all, delete-orphan"` and *could* cascade a delete if this job
    used `db.delete(chat_session)` on a session carrying a ticket:
    a `SupportTicket` (and its `Notification`s) is a separate
    compliance/audit record -- ticket status, assigned agent, resolution
    history -- with its own retention requirements, independent of and
    typically longer-lived than an ephemeral AI chat log. Auto-deleting a
    customer's ticket (open, or already resolved and potentially needed
    for an audit trail) purely as a side effect of the *originating chat
    session* timing out would destroy data this job has no business
    deleting. So: any expired `ChatSession` that has an associated
    `SupportTicket` is deliberately left untouched by this job (both the
    session and the ticket survive). (This also matches the schema's own
    constraint: `SupportTicket.chat_session_id` is a NOT-NULL FK, so the
    `ChatSession` row could not be deleted without cascading into the
    ticket anyway -- skipping it is the only option that doesn't touch the
    ticket.)

    Returns the number of `ChatSession` rows actually deleted.
    """
    reference_now = now if now is not None else datetime.now(timezone.utc)
    cutoff = reference_now - timedelta(minutes=timeout_minutes)

    purged = 0
    for chat_session in db.query(ChatSession).all():
        reference_time = _as_utc(
            chat_session.end_time if chat_session.end_time is not None else chat_session.start_time
        )
        if reference_time > cutoff:
            continue  # not yet expired

        if chat_session.support_ticket is not None:
            # Expired, but has directly-owned data (a SupportTicket) that is
            # out of scope for this job to delete -- see docstring.
            logger.info(
                "Skipping expired ChatSession %s: has an associated SupportTicket %s "
                "that must be retained independently of chat session retention.",
                chat_session.chat_session_id,
                chat_session.support_ticket.support_ticket_id,
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
