"""Custom SQLAlchemy types for the models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A DateTime type that always returns timezone-aware datetimes in UTC.

    This ensures that even when using SQLite (which doesn't natively support
    timezone-aware datetimes), we always get back a datetime with tzinfo set
    to UTC.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self):
        super().__init__(timezone=True)

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        """Convert timezone-aware datetime to UTC naive datetime for storage."""
        if value is not None:
            if value.tzinfo is not None:
                # Convert to UTC and remove tzinfo for storage
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        return None

    def process_result_value(self, value, dialect):  # noqa: ANN001
        """Convert stored naive datetime back to timezone-aware UTC datetime."""
        if value is not None:
            # Assume stored value is in UTC, add UTC tzinfo
            return value.replace(tzinfo=timezone.utc)
        return None
