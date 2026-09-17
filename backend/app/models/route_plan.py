"""RoutePlan model: persists every route computed via `POST /route-plan`
(both the dedicated route-selector page and the chat route-plan intent),
so the day's planned routes and their warnings can be listed
(`GET /route-plans`) and summarized for a manager (the chat "today's
routes" intent). See
docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.

Not the same thing as `AuditLog`'s `route_plan_generated` entries (see
`app.security.audit`) -- those are a free-text compliance trail; this table
is the structured record `GET /route-plans` and the chat intent actually
read back.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import RoutePlanStatus
from app.models.types import UtcDateTime

if TYPE_CHECKING:
    from app.models.customer import Customer


class RoutePlan(Base):
    """A route plan computed and saved for a customer, either via the
    route-selector page (`POST /route-plan`) or the chat route-plan
    intent (`POST /chat`)."""

    __tablename__ = "route_plans"

    route_plan_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"), nullable=False)
    # "customer" / "support_agent" -- who actually submitted this plan,
    # which may differ from customer_id when a support_agent plans a route
    # on a customer's behalf. Plain string, same convention as
    # AuditLog.actor_role (see app.security.audit).
    created_by_role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[int] = mapped_column(nullable=False)
    origin_label: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_label: Mapped[str] = mapped_column(String(255), nullable=False)
    distance_km: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    duration_min: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # List of {"location": {"lat", "lon"}, "distance_from_origin_km",
    # "type", "severity", "description"} dicts -- same shape as
    # WarningOut in app/api/route_plan.py, stored as JSON rather than a
    # child table since these are never queried/filtered individually.
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unavailable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unavailable_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[RoutePlanStatus] = mapped_column(
        Enum(
            RoutePlanStatus,
            name="route_plan_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RoutePlanStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    customer: Mapped["Customer"] = relationship("Customer")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"RoutePlan(route_plan_id={self.route_plan_id!r}, status={self.status!r})"
