"""Tests for `app.timeutil`'s Sydney-local "today" boundary helpers."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.timeutil import site_day_bounds


def test_site_day_bounds_covers_early_morning_sydney_local_time():
    """A route saved at 7am AEDT (Sydney summer, UTC+11) on 2026-01-15 is
    20:00 UTC on 2026-01-14 -- this must still fall inside the Sydney-local
    "2026-01-15" window, which is exactly the bug this module fixes."""
    start_utc, end_utc = site_day_bounds(date(2026, 1, 15))
    early_morning_local_row = datetime(2026, 1, 14, 20, 0, 0, tzinfo=timezone.utc)

    assert start_utc <= early_morning_local_row < end_utc


def test_site_day_bounds_excludes_the_next_calendar_day():
    start_utc, end_utc = site_day_bounds(date(2026, 1, 15))
    next_day_local_row = datetime(2026, 1, 15, 14, 0, 1, tzinfo=timezone.utc)

    assert not (start_utc <= next_day_local_row < end_utc)


def test_site_day_bounds_handles_aest_winter_offset():
    """Sydney winter (AEST, no DST) is UTC+10, not +11 -- confirm the bounds
    shift correctly rather than hardcoding the summer offset."""
    start_utc, end_utc = site_day_bounds(date(2026, 6, 15))

    assert start_utc == datetime(2026, 6, 14, 14, 0, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
