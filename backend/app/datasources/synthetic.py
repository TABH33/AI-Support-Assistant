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

RBAC (roles, tokens, `CurrentUser`) is deliberately absent -- this class
takes a plain `Session` and plain `int` ids/customer_id, same shape as the
plan's Task 8 repository module (`app/repositories/chat.py`). Tenant
*scoping*, however, is present: each ID-keyed method accepts the optional
`customer_id` documented in `base.py` and, when given, applies the same
transitive-join filtering `app/api/telematics.py` (Task 7)'s `_scoped_*`
helpers already use -- `Driver`/`Vehicle` filter directly on their own
`customer_id` column; `Trip`/`DrivingEvent` (no `customer_id` column of
their own) join to `Driver` and filter there, exactly mirroring Task 7's
`_scoped_trips`. Passing `customer_id=None` skips that filter entirely,
reproducing the unscoped query.
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

    def get_driver(self, driver_id: int, customer_id: int | None = None) -> Driver | None:
        query = self._db.query(Driver).filter(Driver.driver_id == driver_id)
        if customer_id is not None:
            query = query.filter(Driver.customer_id == customer_id)
        return query.one_or_none()

    def get_vehicle(self, vehicle_id: int, customer_id: int | None = None) -> Vehicle | None:
        query = self._db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id)
        if customer_id is not None:
            query = query.filter(Vehicle.customer_id == customer_id)
        return query.one_or_none()

    def get_trip(self, trip_id: int, customer_id: int | None = None) -> Trip | None:
        query = self._db.query(Trip).filter(Trip.trip_id == trip_id)
        if customer_id is not None:
            query = query.join(Driver, Trip.driver_id == Driver.driver_id).filter(
                Driver.customer_id == customer_id
            )
        return query.one_or_none()

    def get_trips_for_driver(
        self, driver_id: int, customer_id: int | None = None
    ) -> list[Trip]:
        query = self._db.query(Trip).filter(Trip.driver_id == driver_id)
        if customer_id is not None:
            query = query.join(Driver, Trip.driver_id == Driver.driver_id).filter(
                Driver.customer_id == customer_id
            )
        return query.order_by(Trip.trip_id).all()

    def get_driving_events(
        self, trip_id: int, customer_id: int | None = None
    ) -> list[DrivingEvent]:
        query = self._db.query(DrivingEvent).filter(DrivingEvent.trip_id == trip_id)
        if customer_id is not None:
            query = (
                query.join(Trip, DrivingEvent.trip_id == Trip.trip_id)
                .join(Driver, Trip.driver_id == Driver.driver_id)
                .filter(Driver.customer_id == customer_id)
            )
        return query.order_by(DrivingEvent.event_time).all()

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
