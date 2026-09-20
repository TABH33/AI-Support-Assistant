"""Timezone-aware "today" boundary helpers, shared by every module that
needs to filter rows by calendar day.

This app's deployment/demo data is Sydney, Australia (see
`app/seed/generator.py`'s `DEMO_CORRIDORS`). Filtering "today" on the raw
UTC calendar date (`created_at.date() == datetime.utcnow().date()`) silently
drops early-morning Sydney-local rows from "today" until UTC's date rolls
over -- Sydney (AEST/AEDT) runs 10-11 hours ahead of UTC, so a route planned
at 7am local time is still "yesterday" in UTC until 9-10am local. Every
caller that means "today" for a Sydney-based user should use `site_today()`/
`site_day_bounds()` here instead of computing its own UTC-date filter.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

#: This app's single deployment timezone. Not user-configurable -- there is
#: no per-customer timezone field anywhere in the schema, so one fixed
#: timezone matching the demo data is the correct scope for this POC.
SITE_TZ = ZoneInfo("Australia/Sydney")


def site_today() -> date_:
    """Today's calendar date in `SITE_TZ`, computed from the real current
    time."""
    return datetime.now(timezone.utc).astimezone(SITE_TZ).date()


def site_day_bounds(target_date: date_) -> tuple[datetime, datetime]:
    """`(start_utc, end_utc)`: a half-open UTC range spanning `target_date`
    (a `SITE_TZ` calendar date), suitable for a
    `column >= start_utc AND column < end_utc` filter against a
    `DateTime(timezone=True)` column. Unlike a `func.date(column) == ...`
    predicate, this is timezone-explicit end to end and can use a plain
    index on `column`."""
    local_midnight = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=SITE_TZ
    )
    start_utc = local_midnight.astimezone(timezone.utc)
    end_utc = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)
    return start_utc, end_utc
