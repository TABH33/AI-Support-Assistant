"""`SyntheticDataSource`: the `TelematicsDataSource` implementation backed by
this plan's Postgres tables (Task 4's schema, Task 5's synthetic seed data).

"Synthetic" names the seed data this POC runs against, not the interface --
`TelematicsDataSource` itself (`base.py`) has no opinion about where the
data comes from. This class is a thin wrapper: every method is a direct
SQLAlchemy query against the models in `app.models.telematics` /
`app.models.knowledge`, following the same query shapes already established
in `app/api/telematics.py` (Task 7) -- e.g. `get_driving_events` mirrors
`list_trip_events`'s `db.query(DrivingEvent).filter(...).order_by(...)`
almost verbatim. No caching, no new query logic invented here.

RBAC/customer-scoping is deliberately absent (see `base.py`'s docstring) --
this class takes a plain `Session` and IDs, same as the plan's Task 8
repository module (`app/repositories/chat.py`), not a `CurrentUser`.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.datasources.base import TelematicsDataSource
from app.database import get_db
from app.models.knowledge import KnowledgeBaseArticle
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle


class SyntheticDataSource(TelematicsDataSource):
    """`TelematicsDataSource` backed by the app's own Postgres tables via a
    plain SQLAlchemy `Session`."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_driver(self, driver_id: int) -> Driver | None:
        return self._db.get(Driver, driver_id)

    def get_vehicle(self, vehicle_id: int) -> Vehicle | None:
        return self._db.get(Vehicle, vehicle_id)

    def get_trip(self, trip_id: int) -> Trip | None:
        return self._db.get(Trip, trip_id)

    def get_trips_for_driver(self, driver_id: int) -> list[Trip]:
        return (
            self._db.query(Trip)
            .filter(Trip.driver_id == driver_id)
            .order_by(Trip.trip_id)
            .all()
        )

    def get_driving_events(self, trip_id: int) -> list[DrivingEvent]:
        return (
            self._db.query(DrivingEvent)
            .filter(DrivingEvent.trip_id == trip_id)
            .order_by(DrivingEvent.event_time)
            .all()
        )

    def get_knowledge_base_articles(
        self, category: str | None = None
    ) -> list[KnowledgeBaseArticle]:
        query = self._db.query(KnowledgeBaseArticle)
        if category is not None:
            query = query.filter(KnowledgeBaseArticle.category == category)
        return query.order_by(KnowledgeBaseArticle.knowledge_base_article_id).all()


def get_data_source(db: Session = Depends(get_db)) -> TelematicsDataSource:
    """FastAPI dependency: `Depends(get_data_source)` gives Tasks 11-16 a
    `TelematicsDataSource` without ever importing `app.models` or `Session`
    themselves -- the one place this app currently binds the interface to
    its concrete (Postgres-backed) implementation. Swapping in a future
    `DatabricksDataSource` means changing this one function, not every
    call site that depends on `TelematicsDataSource`."""
    return SyntheticDataSource(db)
