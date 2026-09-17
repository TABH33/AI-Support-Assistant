# Route Selector + Daily Route/Risk Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every route computed via `POST /route-plan` (both the dedicated route-selector page and the chat route-plan intent) as a `RoutePlan` row, expose it through `GET /route-plans` and `PATCH /route-plans/{id}/complete`, flesh out the `Routes.tsx` placeholder into a real selector + tracker UI, and add a chat intent that reports on today's routes.

**Architecture:** One new SQLAlchemy model/table (`RoutePlan`) plus a shared `save_route_plan()` helper in `app/ai/route_planning.py`, called from both `POST /route-plan` and the chat route-plan intent so every route planned through either surface is tracked identically. Two new endpoints (`GET /route-plans`, `PATCH /route-plans/{id}/complete`) reuse the existing RBAC/scoping conventions from `app/api/reports.py` and `app/api/chat.py`'s `GET /tickets`. A new chat intent (`app/ai/route_planning.py`'s `summarize_todays_routes()`) answers "what routes were used today" with a **deterministic, non-LLM** summary.

**Tech Stack:** FastAPI + SQLAlchemy 2.x + Alembic (backend, Python 3.11+), React 18 + TypeScript + Vite + Tailwind (frontend), pytest + Vitest/Testing Library (tests). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md`

## Global Constraints

- `POST /route-plan`'s `customer_id`: ignored (forced to `current_user.user_id`) for a `customer` caller; **required** (400 if omitted) for a `support_agent` caller — exact mirror of `ReportRequest.customer_id` in `backend/app/api/reports.py`.
- `GET /route-plans` with no `customer_id` given: a `customer` caller always sees only their own; a `support_agent` caller sees **all customers'** routes (fleet-wide) — mirrors `GET /tickets`/`GET /notifications`'s unscoped-means-all convention.
- `RoutePlan.status` is `active`/`completed`. The only transition is **manual**, via `PATCH /route-plans/{id}/complete`. No automatic expiry.
- The chat "today's routes" summary makes **no LLM call** — deterministic text built directly from the DB rows, so a warning count/severity can never be reworded or hallucinated.
- Chat intent check order: route-plan intent (plan a *new* route) → today's-routes intent (new) → report intent → RAG fallback.
- No pagination on `GET /route-plans` and no editing of a saved `RoutePlan` after creation (explicitly out of scope per the spec).

---

## Task 1: `RoutePlan` model, status enum, and migration

**Files:**
- Modify: `backend/app/models/enums.py`
- Create: `backend/app/models/route_plan.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/7c3e9a2f1b44_add_route_plans_table.py`
- Test: `backend/tests/test_route_plan_model.py`

**Interfaces:**
- Produces: `RoutePlanStatus` enum (`ACTIVE`/`COMPLETED`, values `"active"`/`"completed"`) in `app.models.enums`. `RoutePlan` model in `app.models.route_plan` with fields: `route_plan_id: int`, `customer_id: int`, `created_by_role: str`, `created_by_id: int`, `origin_label: str`, `destination_label: str`, `distance_km: float | None`, `duration_min: float | None`, `geometry: dict | None`, `warnings: list` (JSON, default `[]`), `unavailable: bool`, `unavailable_reason: str | None`, `status: RoutePlanStatus` (default `ACTIVE`), `created_at: datetime`, `completed_at: datetime | None`. Both exported from `app.models`.

- [ ] **Step 1: Add `RoutePlanStatus` to `backend/app/models/enums.py`**

Append at the end of the file:

```python
class RoutePlanStatus(str, Enum):
    """Whether a saved `RoutePlan` (see `app.models.route_plan`) is still
    being tracked for the day it was created, or has been manually marked
    complete via `PATCH /route-plans/{id}/complete`."""

    ACTIVE = "active"
    COMPLETED = "completed"
```

- [ ] **Step 2: Write the failing model test**

Create `backend/tests/test_route_plan_model.py`:

```python
"""Tests for the `RoutePlan` model (backend/app/models/route_plan.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password
from app.models import Base, Customer, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


def _make_customer(db_session: Session) -> Customer:
    customer = Customer(
        full_name="Route Model Customer",
        email="route-model-customer@example.test",
        phone_number="+61000000001",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def test_route_plan_round_trips_through_the_database(db_session):
    customer = _make_customer(db_session)
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=23.4,
        duration_min=38.2,
        geometry={"type": "LineString", "coordinates": [[151.2, -33.8]]},
        warnings=[{"type": "risk_zone", "severity": "high"}],
        unavailable=False,
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)

    assert route_plan.route_plan_id is not None
    assert route_plan.status == RoutePlanStatus.ACTIVE
    assert route_plan.completed_at is None
    assert route_plan.warnings == [{"type": "risk_zone", "severity": "high"}]
    assert route_plan.created_at.tzinfo is not None


def test_route_plan_can_be_marked_completed(db_session):
    customer = _make_customer(db_session)
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Bondi Beach",
        unavailable=False,
        warnings=[],
    )
    db_session.add(route_plan)
    db_session.commit()

    route_plan.status = RoutePlanStatus.COMPLETED
    route_plan.completed_at = datetime.now(timezone.utc)
    db_session.commit()
    db_session.refresh(route_plan)

    assert route_plan.status == RoutePlanStatus.COMPLETED
    assert route_plan.completed_at is not None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_route_plan_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'RoutePlan' from 'app.models'`

- [ ] **Step 4: Create the model**

Create `backend/app/models/route_plan.py`:

```python
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

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import RoutePlanStatus

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
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped["Customer"] = relationship("Customer")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"RoutePlan(route_plan_id={self.route_plan_id!r}, status={self.status!r})"
```

- [ ] **Step 5: Register the model in `backend/app/models/__init__.py`**

```python
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.chat import ChatMessage, ChatSession, Notification, SupportTicket
from app.models.customer import Customer
from app.models.device import Device
from app.models.knowledge import KnowledgeBaseArticle
from app.models.route_plan import RoutePlan
from app.models.support_agent import SupportAgent
from app.models.telematics import DrivingEvent, Driver, Trip, Vehicle

__all__ = [
    "Base",
    "Customer",
    "Device",
    "Driver",
    "Vehicle",
    "Trip",
    "DrivingEvent",
    "ChatSession",
    "ChatMessage",
    "SupportTicket",
    "Notification",
    "SupportAgent",
    "KnowledgeBaseArticle",
    "AuditLog",
    "RoutePlan",
]
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_route_plan_model.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Create the Alembic migration**

Create `backend/alembic/versions/7c3e9a2f1b44_add_route_plans_table.py`:

```python
"""add_route_plans_table

Revision ID: 7c3e9a2f1b44
Revises: f8e6b5aa61ed
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3e9a2f1b44'
down_revision: Union[str, Sequence[str], None] = 'f8e6b5aa61ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'route_plans',
        sa.Column('route_plan_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('created_by_role', sa.String(length=32), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('origin_label', sa.String(length=255), nullable=False),
        sa.Column('destination_label', sa.String(length=255), nullable=False),
        sa.Column('distance_km', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('duration_min', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('geometry', sa.JSON(), nullable=True),
        sa.Column('warnings', sa.JSON(), nullable=False),
        sa.Column('unavailable', sa.Boolean(), nullable=False),
        sa.Column('unavailable_reason', sa.String(length=32), nullable=True),
        sa.Column(
            'status',
            sa.Enum('active', 'completed', name='route_plan_status'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id']),
        sa.PrimaryKeyConstraint('route_plan_id'),
    )
    op.create_index(op.f('ix_route_plans_customer_id'), 'route_plans', ['customer_id'])
    op.create_index(op.f('ix_route_plans_created_at'), 'route_plans', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_route_plans_created_at'), table_name='route_plans')
    op.drop_index(op.f('ix_route_plans_customer_id'), table_name='route_plans')
    op.drop_table('route_plans')
    sa.Enum(name='route_plan_status').drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/enums.py backend/app/models/route_plan.py backend/app/models/__init__.py backend/alembic/versions/7c3e9a2f1b44_add_route_plans_table.py backend/tests/test_route_plan_model.py
git commit -m "Add RoutePlan model, status enum, and migration"
```

---

## Task 2: Persist route plans from `POST /route-plan`

**Files:**
- Modify: `backend/app/ai/route_planning.py`
- Modify: `backend/app/api/route_plan.py`
- Test: `backend/tests/test_route_plan_api.py`

**Interfaces:**
- Consumes: `RoutePlan` model, `RoutePlanStatus` (Task 1). `RoutePlanResult`, `Warning` (existing, `app.ai.route_planning`).
- Produces: `save_route_plan(db, result, *, customer_id, created_by_role, created_by_id, origin_label, destination_label) -> RoutePlan` in `app.ai.route_planning` — flushes (does not commit), matching `record_audit_event`'s convention. `route_plan_result_to_response(result, *, route_plan_id)` (signature change: now requires `route_plan_id`). `RoutePlanResponse.route_plan_id: int` (new required field). This task's `route_plan_id` and `save_route_plan` are consumed by Task 5 (chat.py).

- [ ] **Step 1: Write the failing tests**

Add to the top of `backend/tests/test_route_plan_api.py`: change the existing `from app.models import AuditLog, Base, Customer` line to:

```python
from app.models import AuditLog, Base, Customer, RoutePlan
```

Then append these tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# Task 2: POST /route-plan persists a RoutePlan row and returns its id.
# ---------------------------------------------------------------------------


def test_successful_route_plan_is_persisted_and_returns_its_id(client, customer_headers, db_session):
    result = RoutePlanResult(distance_km=23.4, duration_min=38.2, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=customer_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["route_plan_id"], int)

    saved = db_session.get(RoutePlan, body["route_plan_id"])
    assert saved is not None
    assert saved.origin_label == "Sydney CBD"
    assert saved.destination_label == "Parramatta"
    assert saved.status.value == "active"
    assert saved.created_by_role == "customer"


def test_unavailable_route_plan_is_still_persisted(client, customer_headers, db_session):
    result = RoutePlanResult(distance_km=None, duration_min=None, geometry=None, unavailable=True)
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Nowhere", "destination": "Parramatta"},
            headers=customer_headers,
        )

    body = response.json()
    saved = db_session.get(RoutePlan, body["route_plan_id"])
    assert saved is not None
    assert saved.unavailable is True


def test_support_agent_must_supply_customer_id(client, db_session):
    from app.auth.security import create_access_token

    agent_token = create_access_token(subject=1, role="support_agent")
    headers = {"Authorization": f"Bearer {agent_token}"}

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={"origin": "Sydney CBD", "destination": "Parramatta"},
            headers=headers,
        )

    assert response.status_code == 400
    assert "customer_id" in response.json()["detail"]


def test_support_agent_can_save_a_route_plan_for_a_named_customer(client, db_session):
    from app.auth.security import create_access_token, hash_password
    from app.models.enums import PreferredNotificationMethod

    customer = Customer(
        full_name="Agent-Scoped Customer",
        email="agent-scoped-customer@example.test",
        phone_number="+61000000002",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)

    agent_token = create_access_token(subject=99, role="support_agent")
    headers = {"Authorization": f"Bearer {agent_token}"}

    result = RoutePlanResult(distance_km=5.0, duration_min=10.0, geometry=_GEOMETRY, warnings=[])
    with patch("app.api.route_plan.build_route_plan", return_value=result):
        response = client.post(
            "/route-plan",
            json={
                "origin": "Sydney CBD",
                "destination": "Parramatta",
                "customer_id": customer.customer_id,
            },
            headers=headers,
        )

    assert response.status_code == 200
    saved = db_session.get(RoutePlan, response.json()["route_plan_id"])
    assert saved.customer_id == customer.customer_id
    assert saved.created_by_role == "support_agent"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_route_plan_api.py -v`
Expected: FAIL — `route_plan_id` missing from the response body (`KeyError`/`AssertionError`), and `customer_id`-related tests fail because the field doesn't exist yet.

- [ ] **Step 3: Add `save_route_plan()` to `backend/app/ai/route_planning.py`**

Add these imports near the top of the file (alongside the existing `from app.geo import haversine_distance_km` line):

```python
from datetime import datetime, timezone

from app.models.enums import RoutePlanStatus
from app.models.route_plan import RoutePlan
```

Add this function after `build_route_plan` (before `build_route_summary_prompt`):

```python
def save_route_plan(
    db: Session,
    result: RoutePlanResult,
    *,
    customer_id: int,
    created_by_role: str,
    created_by_id: int,
    origin_label: str,
    destination_label: str,
) -> RoutePlan:
    """Persists `result` as a `RoutePlan` row so it can later be listed
    (`GET /route-plans`) or summarized for the day it was created
    (`summarize_todays_routes` below). Called from both `POST /route-plan`
    and the chat route-plan intent (`app/api/chat.py`) so a route planned
    through either surface is tracked identically -- including a failed
    plan (`result.unavailable=True`), which is still worth recording (a
    manager asking "any routes fail to plan today?" needs this).

    Flushes, does not commit -- same single-transaction-per-request
    convention as `record_audit_event` (see `app.security.audit`); the
    caller owns `db.commit()`."""
    route_plan = RoutePlan(
        customer_id=customer_id,
        created_by_role=created_by_role,
        created_by_id=created_by_id,
        origin_label=origin_label,
        destination_label=destination_label,
        distance_km=result.distance_km,
        duration_min=result.duration_min,
        geometry=result.geometry,
        warnings=[
            {
                "location": {"lat": w.latitude, "lon": w.longitude},
                "distance_from_origin_km": w.distance_from_origin_km,
                "type": w.type,
                "severity": w.severity,
                "description": w.description,
            }
            for w in result.warnings
        ],
        unavailable=result.unavailable,
        unavailable_reason=result.unavailable_reason,
        status=RoutePlanStatus.ACTIVE,
    )
    db.add(route_plan)
    db.flush()
    db.refresh(route_plan)
    return route_plan
```

- [ ] **Step 4: Wire persistence into `backend/app/api/route_plan.py`**

Replace the full contents of `backend/app/api/route_plan.py` with:

```python
"""`POST /route-plan` -- computes a route via OpenRouteService, evaluates
weather and historical-risk-zone warnings along it, persists the result as
a `RoutePlan` row, and returns one structured result.
`GET /route-plans` and `PATCH /route-plans/{id}/complete` (Tasks 3-4) list
and complete those saved rows. See
docs/superpowers/specs/2026-08-26-route-planning-warnings-design.md and
docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.

RBAC (mirrors every other route in this app): require_role("customer",
"support_agent"). Route/weather/risk-zone *computation* isn't
customer-owned (any authenticated user can plan a route anywhere) -- but
*persistence* is: `customer_id` resolution below mirrors
`app/api/reports.py`'s `ReportRequest` exactly (ignored for a `customer`
caller, required for a `support_agent` caller), since a saved RoutePlan row
always belongs to one customer's daily route/risk record.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.route_planning import RoutePlanResult, Warning, build_route_plan, save_route_plan
from app.auth.dependencies import CurrentUser, require_role
from app.database import get_db
from app.integrations.openrouteservice import Coordinates
from app.models.enums import RoutePlanStatus
from app.models.route_plan import RoutePlan
from app.security.audit import ACTION_ROUTE_PLAN_GENERATED, record_audit_event

router = APIRouter(tags=["route-plan"])

_allowed_roles = require_role("customer", "support_agent")


class CoordinatesIn(BaseModel):
    lat: float
    lon: float


class RoutePlanRequest(BaseModel):
    origin: str | CoordinatesIn
    destination: str | CoordinatesIn
    waypoints: list[str | CoordinatesIn] | None = None
    #: Only honored for a `support_agent`-role caller (required for that
    #: role -- support agents have no fleet of their own to default to).
    #: Ignored for `customer`-role callers, who are always scoped to their
    #: own JWT-derived customer_id. Mirrors `app/api/reports.py`'s
    #: `ReportRequest.customer_id` exactly.
    customer_id: int | None = None


class WarningOut(BaseModel):
    location: dict[str, float]
    distance_from_origin_km: float
    type: str
    severity: str
    description: str


class RoutePlanResponse(BaseModel):
    route_plan_id: int
    distance_km: float | None
    duration_min: float | None
    geometry: dict | None
    warnings: list[WarningOut]
    unavailable: bool
    #: Final-review Fix 5. When `unavailable` is True these say WHY, so a
    #: client can distinguish "that place name doesn't exist" (retrying is
    #: pointless -- fix the spelling) from "the routing service is down"
    #: (retrying shortly is exactly the right advice). Both are None on a
    #: successful plan. `unavailable_reason` is a stable machine-readable
    #: code (see route_planning.UNAVAILABLE_REASON_*);
    #: `unavailable_message` is the display text.
    unavailable_reason: str | None = None
    unavailable_message: str | None = None


class RoutePlanListItem(BaseModel):
    route_plan_id: int
    customer_id: int
    origin_label: str
    destination_label: str
    distance_km: float | None
    duration_min: float | None
    geometry: dict | None
    warnings: list[WarningOut]
    unavailable: bool
    unavailable_reason: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None


class RoutePlanCompleteResponse(BaseModel):
    route_plan_id: int
    status: str
    completed_at: datetime


def _to_origin_input(value: str | CoordinatesIn) -> str | Coordinates:
    if isinstance(value, CoordinatesIn):
        return Coordinates(latitude=value.lat, longitude=value.lon)
    return value


def _to_place_label(value: str | CoordinatesIn) -> str:
    if isinstance(value, CoordinatesIn):
        return f"{value.lat},{value.lon}"
    return value


def _to_warning_out(warning: Warning) -> WarningOut:
    return WarningOut(
        location={"lat": warning.latitude, "lon": warning.longitude},
        distance_from_origin_km=warning.distance_from_origin_km,
        type=warning.type,
        severity=warning.severity,
        description=warning.description,
    )


def route_plan_result_to_response(result: RoutePlanResult, *, route_plan_id: int) -> RoutePlanResponse:
    """Shared JSON-shaping function -- also called from app/api/chat.py so
    the ChatResponse.route_plan field uses the exact same shape as this
    endpoint's own response, without duplicating this mapping."""
    return RoutePlanResponse(
        route_plan_id=route_plan_id,
        distance_km=result.distance_km,
        duration_min=result.duration_min,
        geometry=result.geometry,
        warnings=[_to_warning_out(w) for w in result.warnings],
        unavailable=result.unavailable,
        unavailable_reason=result.unavailable_reason,
        unavailable_message=result.unavailable_message,
    )


def _resolve_customer_id(payload: RoutePlanRequest, current_user: CurrentUser) -> int:
    if current_user.role == "customer":
        return current_user.user_id
    if payload.customer_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="customer_id is required for a support_agent to save a route plan",
        )
    return payload.customer_id


def _row_to_list_item(row: RoutePlan) -> RoutePlanListItem:
    return RoutePlanListItem(
        route_plan_id=row.route_plan_id,
        customer_id=row.customer_id,
        origin_label=row.origin_label,
        destination_label=row.destination_label,
        distance_km=float(row.distance_km) if row.distance_km is not None else None,
        duration_min=float(row.duration_min) if row.duration_min is not None else None,
        geometry=row.geometry,
        warnings=[WarningOut(**w) for w in row.warnings],
        unavailable=row.unavailable,
        unavailable_reason=row.unavailable_reason,
        status=row.status.value,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


@router.post("/route-plan", response_model=RoutePlanResponse)
def post_route_plan(
    payload: RoutePlanRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> RoutePlanResponse:
    customer_id = _resolve_customer_id(payload, current_user)

    origin = _to_origin_input(payload.origin)
    destination = _to_origin_input(payload.destination)
    waypoints = [_to_origin_input(w) for w in payload.waypoints] if payload.waypoints else None

    result = build_route_plan(origin, destination, waypoints, db=db)

    route_plan_row = save_route_plan(
        db,
        result,
        customer_id=customer_id,
        created_by_role=current_user.role,
        created_by_id=current_user.user_id,
        origin_label=_to_place_label(payload.origin),
        destination_label=_to_place_label(payload.destination),
    )

    record_audit_event(
        db,
        actor_id=current_user.user_id,
        actor_role=current_user.role,
        action=ACTION_ROUTE_PLAN_GENERATED,
        description=(
            f"origin={payload.origin!r} destination={payload.destination!r} "
            f"unavailable={result.unavailable} reason={result.unavailable_reason}"
        ),
    )
    db.commit()

    return route_plan_result_to_response(result, route_plan_id=route_plan_row.route_plan_id)


@router.get("/route-plans", response_model=list[RoutePlanListItem])
def get_route_plans(
    date_: date | None = Query(None, alias="date"),
    status_filter: RoutePlanStatus | None = Query(None, alias="status"),
    customer_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> list[RoutePlanListItem]:
    target_date = date_ or datetime.now(timezone.utc).date()
    query = db.query(RoutePlan).filter(func.date(RoutePlan.created_at) == target_date)

    if current_user.role == "customer":
        query = query.filter(RoutePlan.customer_id == current_user.user_id)
    elif customer_id is not None:
        query = query.filter(RoutePlan.customer_id == customer_id)

    if status_filter is not None:
        query = query.filter(RoutePlan.status == status_filter)

    rows = query.order_by(RoutePlan.created_at.desc()).all()
    return [_row_to_list_item(row) for row in rows]


@router.patch("/route-plans/{route_plan_id}/complete", response_model=RoutePlanCompleteResponse)
def complete_route_plan(
    route_plan_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> RoutePlanCompleteResponse:
    route_plan = db.get(RoutePlan, route_plan_id)
    if route_plan is None or (
        current_user.role == "customer" and route_plan.customer_id != current_user.user_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Route plan not found")
    if route_plan.status == RoutePlanStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Route plan is already completed")

    route_plan.status = RoutePlanStatus.COMPLETED
    route_plan.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(route_plan)

    return RoutePlanCompleteResponse(
        route_plan_id=route_plan.route_plan_id,
        status=route_plan.status.value,
        completed_at=route_plan.completed_at,
    )
```

(This step includes Tasks 3 and 4's endpoints already, since they live in the same small file and the file is being fully rewritten here — Tasks 3/4 below add their own tests against this same code, already in place.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_route_plan_api.py -v`
Expected: PASS (all tests, including the 4 new ones)

- [ ] **Step 6: Note the chat.py call-site dependency**

`backend/app/api/chat.py` line ~55 imports `route_plan_result_to_response`, and line ~446 calls it without the now-required `route_plan_id` keyword. This is fixed together with the rest of the chat wiring in Task 5, not here — leave it as-is for this task.

- [ ] **Step 7: Run the full backend test suite to confirm the scope of what's still broken**

Run: `cd backend && pytest -v`
Expected: All tests pass **except** any in `backend/tests/test_chat_api.py` that exercise the route-plan intent (they fail on the now-mismatched `route_plan_result_to_response` call until Task 5). Confirm the failures are limited to that one file/intent before proceeding.

- [ ] **Step 8: Commit**

```bash
git add backend/app/ai/route_planning.py backend/app/api/route_plan.py backend/tests/test_route_plan_api.py
git commit -m "Persist RoutePlan rows from POST /route-plan; add GET /route-plans and PATCH .../complete"
```

---

## Task 3: Tests for `GET /route-plans` scoping

**Files:**
- Test: `backend/tests/test_route_plans_list_api.py`

**Interfaces:**
- Consumes: `GET /route-plans` (implemented in Task 2's rewrite of `route_plan.py`), `RoutePlan` model (Task 1).

The endpoint itself was written in Task 2 (the file was fully rewritten there to keep `route_plan.py` internally consistent in one pass). This task adds the dedicated scoping test coverage the spec calls for.

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_route_plans_list_api.py`:

```python
"""Tests for `GET /route-plans` (Task 3 of the route-selector +
daily-tracking plan): role-based scoping and date/status filtering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, Customer, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(engine, db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        from fastapi.testclient import TestClient

        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"List Customer {tag}",
        email=f"list-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_route_plan(
    db_session: Session,
    *,
    customer: Customer,
    status: RoutePlanStatus = RoutePlanStatus.ACTIVE,
    created_at: datetime | None = None,
) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        distance_km=10.0,
        duration_min=15.0,
        geometry={"type": "LineString", "coordinates": []},
        warnings=[],
        unavailable=False,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def test_customer_sees_only_their_own_routes(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=customer_a.customer_id, role="customer")
    response = client.get("/route-plans", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_a.customer_id


def test_support_agent_sees_all_customers_when_unscoped(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get("/route-plans", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_support_agent_can_narrow_by_customer_id(client, db_session):
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    _make_route_plan(db_session, customer=customer_a)
    _make_route_plan(db_session, customer=customer_b)

    token = create_access_token(subject=1, role="support_agent")
    response = client.get(
        f"/route-plans?customer_id={customer_b.customer_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["customer_id"] == customer_b.customer_id


def test_status_filter_narrows_results(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer, status=RoutePlanStatus.ACTIVE)
    _make_route_plan(db_session, customer=customer, status=RoutePlanStatus.COMPLETED)

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get(
        "/route-plans?status=completed", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "completed"


def test_date_filter_excludes_routes_from_other_days(client, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)
    _make_route_plan(
        db_session,
        customer=customer,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )

    token = create_access_token(subject=customer.customer_id, role="customer")
    response = client.get("/route-plans", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_unauthenticated_request_is_rejected(client):
    response = client.get("/route-plans")
    assert response.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_route_plans_list_api.py -v`
Expected: PASS (6 tests) — the endpoint was already implemented in Task 2's rewrite of `route_plan.py`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_route_plans_list_api.py
git commit -m "Add scoping/filtering test coverage for GET /route-plans"
```

---

## Task 4: Tests for `PATCH /route-plans/{id}/complete`

**Files:**
- Test: `backend/tests/test_route_plan_complete_api.py`

**Interfaces:**
- Consumes: `PATCH /route-plans/{id}/complete` (implemented in Task 2's rewrite of `route_plan.py`), `RoutePlan` model (Task 1).

Same situation as Task 3: the endpoint itself already exists from Task 2's rewrite; this task adds its dedicated test coverage (success, wrong-owner 404, already-completed 409).

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_route_plan_complete_api.py`:

```python
"""Tests for `PATCH /route-plans/{id}/complete` (Task 4 of the
route-selector + daily-tracking plan)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, Customer, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(engine, db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        from fastapi.testclient import TestClient

        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Complete Customer {tag}",
        email=f"complete-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_route_plan(db_session: Session, *, customer: Customer) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        warnings=[],
        unavailable=False,
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def test_owning_customer_can_mark_their_route_complete(client, db_session):
    customer = _make_customer(db_session, tag="A")
    route_plan = _make_route_plan(db_session, customer=customer)
    token = create_access_token(subject=customer.customer_id, role="customer")

    response = client.patch(
        f"/route-plans/{route_plan.route_plan_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None

    db_session.refresh(route_plan)
    assert route_plan.status == RoutePlanStatus.COMPLETED


def test_support_agent_can_complete_any_customers_route(client, db_session):
    customer = _make_customer(db_session, tag="A")
    route_plan = _make_route_plan(db_session, customer=customer)
    token = create_access_token(subject=1, role="support_agent")

    response = client.patch(
        f"/route-plans/{route_plan.route_plan_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_customer_cannot_complete_another_customers_route(client, db_session):
    owner = _make_customer(db_session, tag="A")
    other = _make_customer(db_session, tag="B")
    route_plan = _make_route_plan(db_session, customer=owner)
    token = create_access_token(subject=other.customer_id, role="customer")

    response = client.patch(
        f"/route-plans/{route_plan.route_plan_id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_completing_an_already_completed_route_returns_409(client, db_session):
    customer = _make_customer(db_session, tag="A")
    route_plan = _make_route_plan(db_session, customer=customer)
    token = create_access_token(subject=customer.customer_id, role="customer")
    headers = {"Authorization": f"Bearer {token}"}

    first = client.patch(f"/route-plans/{route_plan.route_plan_id}/complete", headers=headers)
    assert first.status_code == 200

    second = client.patch(f"/route-plans/{route_plan.route_plan_id}/complete", headers=headers)
    assert second.status_code == 409


def test_completing_a_nonexistent_route_returns_404(client, db_session):
    customer = _make_customer(db_session, tag="A")
    token = create_access_token(subject=customer.customer_id, role="customer")

    response = client.patch(
        "/route-plans/999999/complete", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_route_plan_complete_api.py -v`
Expected: PASS (5 tests)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_route_plan_complete_api.py
git commit -m "Add test coverage for PATCH /route-plans/{id}/complete"
```

---

## Task 5: Chat integration — save route plans from chat, add "today's routes" intent

**Files:**
- Modify: `backend/app/ai/route_planning.py`
- Modify: `backend/app/api/chat.py`
- Test: `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: `save_route_plan` (Task 2), `RoutePlan`/`RoutePlanStatus` (Task 1), `route_plan_result_to_response` (Task 2, now requires `route_plan_id`).
- Produces: `summarize_todays_routes(db, *, customer_id) -> str` in `app.ai.route_planning`. `_detect_todays_routes_intent(query: str) -> bool` in `app.api.chat`.

- [ ] **Step 1: Add `summarize_todays_routes()` to `backend/app/ai/route_planning.py`**

Change the `from datetime import datetime, timezone` line added in Task 2 to:

```python
from datetime import date, datetime, timezone
```

Add `func` as a new import line, next to the existing `from sqlalchemy.exc import SQLAlchemyError` / `from sqlalchemy.orm import Session` lines:

```python
from sqlalchemy import func
```

Add this function at the end of the file, after `summarize_route_plan`:

```python
def summarize_todays_routes(db: Session, *, customer_id: int | None) -> str:
    """Deterministic (no LLM call) summary of today's RoutePlan rows, for
    the chat "today's routes" intent (app/api/chat.py's
    _detect_todays_routes_intent). customer_id=None means fleet-wide (a
    support_agent asking without narrowing to one customer) -- mirrors
    every other support_agent-facing list endpoint's unscoped-means-all
    convention (see GET /route-plans, GET /tickets).

    Deliberately does NOT call the LLM: the numbers here (warning counts,
    severities) come straight from stored RoutePlan rows, and rewriting
    them through an LLM risks exactly the kind of drift/hallucination this
    app's RAG pipeline already guards against elsewhere (see
    app/ai/chat_service.py's strict "answer only from context" system
    prompt)."""
    today = datetime.now(timezone.utc).date()
    query = db.query(RoutePlan).filter(func.date(RoutePlan.created_at) == today)
    if customer_id is not None:
        query = query.filter(RoutePlan.customer_id == customer_id)
    routes = query.order_by(RoutePlan.created_at.desc()).all()

    if not routes:
        return "No routes have been planned today."

    active_count = sum(1 for r in routes if r.status == RoutePlanStatus.ACTIVE)
    completed_count = len(routes) - active_count

    lines = [
        f"{len(routes)} route(s) planned today "
        f"({active_count} active, {completed_count} completed)."
    ]
    for route in routes:
        status_label = "active" if route.status == RoutePlanStatus.ACTIVE else "completed"
        detail = f"- {route.origin_label} -> {route.destination_label} ({status_label})"
        if route.unavailable:
            detail += ": route data was unavailable when planned"
        elif route.warnings:
            high_severity = sum(1 for w in route.warnings if w.get("severity") == "high")
            detail += f": {len(route.warnings)} warning(s)"
            if high_severity:
                detail += f", {high_severity} high-severity"
        else:
            detail += ": no warnings"
        lines.append(detail)

    return "\n".join(lines)
```

- [ ] **Step 2: Write the failing chat tests**

Change the existing `from app.models import (...)` import block in `backend/tests/test_chat_api.py` to add `RoutePlan`:

```python
from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Customer,
    Device,
    Driver,
    RoutePlan,
    SupportTicket,
    Trip,
    Vehicle,
)
```

And change the existing `from app.models.enums import BatteryStatus, DeviceStatus, PreferredNotificationMethod` line to add `RoutePlanStatus`:

```python
from app.models.enums import BatteryStatus, DeviceStatus, PreferredNotificationMethod, RoutePlanStatus
```

Append these tests at the end of `backend/tests/test_chat_api.py`:

```python
# ---------------------------------------------------------------------------
# Task 5: chat route-plan intent persists a RoutePlan; new "today's routes"
# intent reports on them.
# ---------------------------------------------------------------------------


def test_route_plan_intent_persists_a_route_plan(client, db_session, fleet_a):
    route_result = RoutePlanResult(
        distance_km=23.4, duration_min=38.2, geometry={"type": "LineString", "coordinates": []}, warnings=[]
    )
    with (
        patch("app.api.chat.build_route_plan", return_value=route_result),
        patch("app.api.chat.summarize_route_plan", return_value="It's a 23.4km trip."),
    ):
        response = client.post(
            "/chat",
            json={
                "query": "plan a trip from Sydney CBD to Parramatta",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["route_plan"]["route_plan_id"], int)

    saved = db_session.get(RoutePlan, body["route_plan"]["route_plan_id"])
    assert saved is not None
    assert saved.customer_id == fleet_a["customer"].customer_id
    assert saved.origin_label == "Sydney CBD"


def test_todays_routes_intent_reports_saved_routes(client, db_session, fleet_a):
    route_plan = RoutePlan(
        customer_id=fleet_a["customer"].customer_id,
        created_by_role="customer",
        created_by_id=fleet_a["customer"].customer_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        warnings=[{"type": "risk_zone", "severity": "high", "description": "x"}],
        unavailable=False,
        status=RoutePlanStatus.ACTIVE,
    )
    db_session.add(route_plan)
    db_session.commit()

    with patch("app.ai.chat_service.chat_completion") as mock_chat:
        response = client.post(
            "/chat",
            json={"query": "what routes were used today?", "device_id": fleet_a["device"].device_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] == 1.0
    assert body["escalated"] is False
    assert "Sydney CBD -> Parramatta" in body["answer"]
    assert "1 high-severity" in body["answer"]
    mock_chat.assert_not_called()


def test_todays_routes_intent_is_fleet_wide_for_support_agent(client, db_session, fleet_a, fleet_b):
    for fleet in (fleet_a, fleet_b):
        db_session.add(
            RoutePlan(
                customer_id=fleet["customer"].customer_id,
                created_by_role="customer",
                created_by_id=fleet["customer"].customer_id,
                origin_label="Sydney CBD",
                destination_label="Parramatta",
                warnings=[],
                unavailable=False,
            )
        )
    db_session.commit()

    from app.auth.security import create_access_token

    agent_token = create_access_token(subject=1, role="support_agent")
    with patch("app.ai.chat_service.chat_completion"):
        response = client.post(
            "/chat",
            json={
                "query": "any active routes today?",
                "device_id": fleet_a["device"].device_id,
                "customer_id": fleet_a["customer"].customer_id,
            },
            headers={"Authorization": f"Bearer {agent_token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "2 route(s) planned today" in body["answer"]


def test_todays_routes_intent_reports_no_routes_when_none_planned(client, fleet_a):
    with patch("app.ai.chat_service.chat_completion") as mock_chat:
        response = client.post(
            "/chat",
            json={"query": "what routes were used today?", "device_id": fleet_a["device"].device_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "No routes have been planned today."
    mock_chat.assert_not_called()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_chat_api.py -v`
Expected: FAIL — `save_route_plan` isn't called yet from `chat.py` (no `route_plan_id` in the response), and the "today's routes" intent doesn't exist yet (falls through to RAG/LLM instead).

- [ ] **Step 4: Wire it into `backend/app/api/chat.py`**

Update the import block (around line 49-55) to add `save_route_plan` and `summarize_todays_routes`:

```python
from app.ai.route_planning import (
    ROUTE_DATA_UNAVAILABLE_TEXT,
    RoutePlanResult,
    build_route_plan,
    save_route_plan,
    summarize_route_plan,
    summarize_todays_routes,
)
```

Add the new intent detector after `_detect_route_plan_intent` (after the closing of that function, before the `# Report-intent routing` comment block around line 279):

```python
# ---------------------------------------------------------------------------
# "Today's routes" intent routing
# ---------------------------------------------------------------------------

# Requires BOTH a route-word and a temporal/status word, so an ordinary
# telematics question that happens to mention "risk" (e.g. "is harsh
# braking a risk for this driver?") doesn't get swallowed here -- and so
# this doesn't collide with _detect_route_plan_intent's "plan a NEW route"
# phrasing, which is checked first (see post_chat below).
_TODAYS_ROUTES_ROUTE_WORDS = ("route", "routes")
_TODAYS_ROUTES_SIGNAL_WORDS = ("today", "active", "risk", "risks")


def _detect_todays_routes_intent(query: str) -> bool:
    """True if the query is asking about already-planned routes (e.g.
    "what routes were used today", "any risk signals for the routes
    today", "active routes") rather than asking to plan a NEW route.
    Deliberately simple keyword matching, same philosophy as
    _detect_report_intent/_detect_route_plan_intent."""
    lowered = query.lower()
    has_route_word = any(word in lowered for word in _TODAYS_ROUTES_ROUTE_WORDS)
    has_signal_word = any(word in lowered for word in _TODAYS_ROUTES_SIGNAL_WORDS)
    return has_route_word and has_signal_word
```

Update the intent-resolution block inside `post_chat` (currently around lines 333-334):

```python
    route_plan_intent = _detect_route_plan_intent(payload.query)
    todays_routes_intent = (
        _detect_todays_routes_intent(payload.query) if route_plan_intent is None else False
    )
    report_intent = (
        _detect_report_intent(payload.query)
        if route_plan_intent is None and not todays_routes_intent
        else None
    )
    route_plan_payload: RoutePlanResult | None = None
    saved_route_plan_id: int | None = None
```

Update the route-plan-intent branch (currently lines ~337-360) to persist the plan and add a new `elif` branch for `todays_routes_intent` right after it:

```python
    if route_plan_intent is not None:
        # Route-plan requests bypass RAG/escalation entirely, same "always
        # delivered" design as report requests -- checked first since its
        # keyword set is more specific than the report one.
        if route_plan_intent.origin is None:
            answer_text = "Which starting point should I plan this route from?"
        else:
            route_result = build_route_plan(
                route_plan_intent.origin, route_plan_intent.destination, db=db
            )
            saved_route_plan = save_route_plan(
                db,
                route_result,
                customer_id=customer_id,
                created_by_role=current_user.role,
                created_by_id=current_user.user_id,
                origin_label=route_plan_intent.origin,
                destination_label=route_plan_intent.destination,
            )
            saved_route_plan_id = saved_route_plan.route_plan_id
            if route_result.unavailable:
                # Final-review Fix 5: prefer the failure-specific message
                # (e.g. "I couldn't find a location matching 'Parramattaa'")
                # over the generic outage text, which is misleading advice
                # when the real problem is a misspelled place name.
                answer_text = route_result.unavailable_message or ROUTE_DATA_UNAVAILABLE_TEXT
            else:
                answer_text = summarize_route_plan(
                    route_result, route_plan_intent.origin, route_plan_intent.destination
                )
                route_plan_payload = route_result
        confidence = 1.0
        escalated = False
        audit_action = ACTION_ROUTE_PLAN_GENERATED
    elif todays_routes_intent:
        # Fleet-wide for a support_agent (ignores this session's own
        # customer_id -- a support_agent has no fleet of their own, same
        # convention as GET /route-plans and GET /tickets), scoped to the
        # caller's own fleet for a customer.
        answer_text = summarize_todays_routes(
            db, customer_id=customer_id if current_user.role == "customer" else None
        )
        confidence = 1.0
        escalated = False
        audit_action = ACTION_ROUTE_PLAN_GENERATED
    elif report_intent is not None:
```

(The `elif report_intent is not None:` line already exists immediately after the old route-plan block — this replaces the `if route_plan_intent is not None: ... elif report_intent is not None:` structure with the three-way branch above; the `elif report_intent is not None:` body itself is unchanged.)

Finally, update the `ChatResponse` construction (currently line 440-447) to pass `route_plan_id`:

```python
    return ChatResponse(
        session_id=session_id,
        message_id=message_id,
        answer=answer_text,
        confidence=confidence,
        escalated=escalated,
        route_plan=(
            route_plan_result_to_response(route_plan_payload, route_plan_id=saved_route_plan_id)
            if route_plan_payload is not None
            else None
        ),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_chat_api.py -v`
Expected: PASS (all tests, including the 4 new ones and the pre-existing route-plan-intent tests)

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && pytest -v`
Expected: PASS (all tests across the whole backend)

- [ ] **Step 7: Commit**

```bash
git add backend/app/ai/route_planning.py backend/app/api/chat.py backend/tests/test_chat_api.py
git commit -m "Wire route-plan persistence and a today's-routes chat intent into POST /chat"
```

---

## Task 6: Frontend — route selector + daily tracker on the Routes page

**Files:**
- Modify: `frontend/src/types/routePlan.ts`
- Modify: `frontend/src/pages/Routes.tsx`
- Create: `frontend/src/pages/Routes.test.tsx`

**Interfaces:**
- Consumes: `GET /route-plans`, `POST /route-plan` (now returns `route_plan_id`), `PATCH /route-plans/{id}/complete` (Tasks 2-4). `RouteMap` component (existing, `frontend/src/components/RouteMap.tsx`). `useAuth()` (existing, `frontend/src/context/AuthProvider.tsx`).

- [ ] **Step 1: Add the new response shapes to `frontend/src/types/routePlan.ts`**

Add `route_plan_id` to the existing `RoutePlanResult` interface (as optional, so `RouteMap.test.tsx`'s existing inline fixtures that omit it keep compiling) and add the new list-item type. Replace the file's contents with:

```typescript
/**
 * TypeScript mirrors of `RoutePlanResponse`/`WarningOut`/`RoutePlanListItem`
 * in `backend/app/api/route_plan.py` (also reused, unchanged, as
 * `ChatResponse.route_plan`'s shape in `backend/app/api/chat.py`). Field
 * names/nullability were copied directly from those Pydantic models, not
 * guessed -- keep in sync if the backend schema changes.
 */
export interface RouteGeometry {
  type: string
  coordinates: [number, number][]
}

export interface RouteWarning {
  location: { lat: number; lon: number }
  distance_from_origin_km: number
  type: 'weather' | 'risk_zone'
  severity: string
  description: string
}

export interface RoutePlanResult {
  /** Optional here only so pre-existing test fixtures that predate this
   * field keep compiling -- every real response includes it. */
  route_plan_id?: number
  distance_km: number | null
  duration_min: number | null
  geometry: RouteGeometry | null
  warnings: RouteWarning[]
  unavailable: boolean
  /**
   * Populated only when `unavailable` is true (final-review Fix 5).
   * `unavailable_reason` is a stable machine-readable code --
   * `'geocoding_failed'` (the place name could not be resolved; retrying
   * will not help, the user must fix the spelling) or
   * `'service_unavailable'` (a downstream outage; retrying shortly is the
   * right advice). `unavailable_message` is the matching display text.
   */
  unavailable_reason?: 'geocoding_failed' | 'service_unavailable' | null
  unavailable_message?: string | null
}

/** `GET /route-plans` list item -- a saved RoutePlan row, mirroring
 * `RoutePlanListItem` in `backend/app/api/route_plan.py`. */
export interface RoutePlanListItem {
  route_plan_id: number
  customer_id: number
  origin_label: string
  destination_label: string
  distance_km: number | null
  duration_min: number | null
  geometry: RouteGeometry | null
  warnings: RouteWarning[]
  unavailable: boolean
  unavailable_reason: string | null
  status: 'active' | 'completed'
  created_at: string
  completed_at: string | null
}
```

- [ ] **Step 2: Write the failing frontend tests**

Create `frontend/src/pages/Routes.test.tsx`:

```typescript
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { AuthProvider, TOKEN_STORAGE_KEY } from '../context/AuthProvider'
import { makeFakeJwt } from '../test-support/jwt'
import RoutesPage from './Routes'
import type { RoutePlanListItem } from '../types/routePlan'

vi.mock('../components/RouteMap', () => ({
  RouteMap: () => <div data-testid="mock-route-map" />,
}))

function renderPage() {
  return render(
    <AuthProvider>
      <RoutesPage />
    </AuthProvider>
  )
}

function loginAsCustomer() {
  const token = makeFakeJwt({
    sub: '100',
    role: 'customer',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

function loginAsSupportAgent() {
  const token = makeFakeJwt({
    sub: '7',
    role: 'support_agent',
    access_level: 'admin',
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 3600,
  })
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

const activeRoute: RoutePlanListItem = {
  route_plan_id: 1,
  customer_id: 100,
  origin_label: 'Sydney CBD',
  destination_label: 'Parramatta',
  distance_km: 23.4,
  duration_min: 38.2,
  geometry: { type: 'LineString', coordinates: [] },
  warnings: [
    {
      location: { lat: -33.8, lon: 151.0 },
      distance_from_origin_km: 10,
      type: 'risk_zone',
      severity: 'high',
      description: 'x',
    },
  ],
  unavailable: false,
  unavailable_reason: null,
  status: 'active',
  created_at: '2026-09-18T08:00:00Z',
  completed_at: null,
}

const completedRoute: RoutePlanListItem = {
  ...activeRoute,
  route_plan_id: 2,
  origin_label: 'Sydney CBD',
  destination_label: 'Bondi Beach',
  status: 'completed',
  warnings: [],
  completed_at: '2026-09-18T09:00:00Z',
}

function mockRoutesFetch(routes: RoutePlanListItem[] = [activeRoute, completedRoute]) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string, options?: RequestInit) => {
    if (options?.method === 'PATCH' && url.includes('/complete')) {
      return { ok: true, status: 200, json: async () => ({ route_plan_id: 1, status: 'completed', completed_at: '2026-09-18T10:00:00Z' }) }
    }
    if (options?.method === 'POST' && url.includes('/route-plan')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          route_plan_id: 3,
          distance_km: 5.0,
          duration_min: 10.0,
          geometry: { type: 'LineString', coordinates: [] },
          warnings: [],
          unavailable: false,
        }),
      }
    }
    if (url.includes('/route-plans')) {
      return { ok: true, status: 200, json: async () => routes }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}

describe('RoutesPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('lists active and completed routes fetched from GET /route-plans', async () => {
    loginAsCustomer()
    mockRoutesFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('route-1')).toHaveTextContent('Sydney CBD → Parramatta')
    })
    expect(screen.getByTestId('route-2')).toHaveTextContent('Sydney CBD → Bondi Beach')
    expect(screen.getByText('Active (1)')).toBeInTheDocument()
    expect(screen.getByText('Completed (1)')).toBeInTheDocument()
  })

  it('marks an active route complete via the Mark complete button', async () => {
    loginAsCustomer()
    mockRoutesFetch()

    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId('route-1')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /mark complete/i }))

    await waitFor(() => {
      const calledUrls = (fetch as unknown as Mock).mock.calls.map(([url, options]) => ({
        url,
        method: options?.method,
      }))
      expect(
        calledUrls.some((c) => c.url.includes('/route-plans/1/complete') && c.method === 'PATCH')
      ).toBe(true)
    })
  })

  it('plans a new route via the form and re-fetches the list', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No active routes today.')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/origin/i), { target: { value: 'Sydney CBD' } })
    fireEvent.change(screen.getByLabelText(/destination/i), { target: { value: 'Bondi Beach' } })
    fireEvent.click(screen.getByRole('button', { name: /plan route/i }))

    await waitFor(() => {
      expect(screen.getByTestId('mock-route-map')).toBeInTheDocument()
    })

    const postCall = (fetch as unknown as Mock).mock.calls.find(
      ([url, options]) => options?.method === 'POST' && (url as string).includes('/route-plan')
    )
    expect(postCall).toBeTruthy()
  })

  it('shows a customer-ID filter only for a support_agent caller', async () => {
    loginAsSupportAgent()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByLabelText(/filter by customer id/i)).toBeInTheDocument()
    })
  })

  it('does not show the customer-ID filter for a customer caller', async () => {
    loginAsCustomer()
    mockRoutesFetch([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No active routes today.')).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/filter by customer id/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npm run test -- Routes.test.tsx`
Expected: FAIL — `Routes.tsx` is still the placeholder, none of the queried elements exist.

- [ ] **Step 4: Rewrite `frontend/src/pages/Routes.tsx`**

Replace the full contents of `frontend/src/pages/Routes.tsx` with:

```typescript
/**
 * Route selector + daily route/risk tracking. Lets a customer (or, on
 * behalf of a customer, a support_agent) plan a route -- reusing the
 * route-planning feature's `POST /route-plan` and `RouteMap` -- and lists
 * everyone's routes planned today via `GET /route-plans`, with a "Mark
 * complete" action (`PATCH /route-plans/{id}/complete`). A support_agent
 * additionally sees every customer's routes and can filter by customer ID
 * -- see docs/superpowers/specs/2026-09-18-route-selector-and-daily-tracking-design.md.
 */
import { useEffect, useState, type FormEvent } from 'react'
import { apiGet, apiPatch, apiPost } from '../lib/apiClient'
import { useAuth } from '../context/AuthProvider'
import { RouteMap } from '../components/RouteMap'
import type { RoutePlanListItem, RoutePlanResult } from '../types/routePlan'

const STATUS_BADGE: Record<'active' | 'completed', string> = {
  active: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
  completed: 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300',
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export default function RoutesPage() {
  const { user } = useAuth()
  const isSupportAgent = user?.role === 'support_agent'

  const [origin, setOrigin] = useState('')
  const [destination, setDestination] = useState('')
  const [planCustomerId, setPlanCustomerId] = useState('')
  const [planResult, setPlanResult] = useState<RoutePlanResult | null>(null)
  const [planError, setPlanError] = useState<string | null>(null)
  const [isPlanning, setIsPlanning] = useState(false)

  const [routes, setRoutes] = useState<RoutePlanListItem[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [isLoadingList, setIsLoadingList] = useState(true)
  const [filterCustomerId, setFilterCustomerId] = useState('')

  async function loadRoutes() {
    setIsLoadingList(true)
    setListError(null)
    try {
      const query =
        isSupportAgent && filterCustomerId
          ? `?customer_id=${encodeURIComponent(filterCustomerId)}`
          : ''
      const data = await apiGet<RoutePlanListItem[]>(`/route-plans${query}`)
      setRoutes(data)
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to load routes.')
    } finally {
      setIsLoadingList(false)
    }
  }

  useEffect(() => {
    void loadRoutes()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterCustomerId])

  async function handlePlanRoute(event: FormEvent) {
    event.preventDefault()
    setPlanError(null)

    if (isSupportAgent && !planCustomerId) {
      setPlanError('Customer ID is required for a support agent to save a route plan.')
      return
    }

    setIsPlanning(true)
    setPlanResult(null)
    try {
      const body: Record<string, unknown> = { origin, destination }
      if (isSupportAgent) {
        body.customer_id = Number(planCustomerId)
      }
      const result = await apiPost<RoutePlanResult>('/route-plan', body)
      setPlanResult(result)
      await loadRoutes()
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : 'Failed to plan route.')
    } finally {
      setIsPlanning(false)
    }
  }

  async function handleMarkComplete(routePlanId: number) {
    try {
      await apiPatch(`/route-plans/${routePlanId}/complete`, {})
      await loadRoutes()
    } catch (err) {
      setListError(err instanceof Error ? err.message : 'Failed to mark route complete.')
    }
  }

  const active = routes.filter((route) => route.status === 'active')
  const completed = routes.filter((route) => route.status === 'completed')

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Routes</h1>

      <form onSubmit={handlePlanRoute} className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="route-origin" className="block text-sm text-gray-600 dark:text-gray-300">
            Origin
          </label>
          <input
            id="route-origin"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            required
            className="mt-1 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
        <div>
          <label
            htmlFor="route-destination"
            className="block text-sm text-gray-600 dark:text-gray-300"
          >
            Destination
          </label>
          <input
            id="route-destination"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            required
            className="mt-1 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
        {isSupportAgent && (
          <div>
            <label
              htmlFor="route-plan-customer-id"
              className="block text-sm text-gray-600 dark:text-gray-300"
            >
              Customer ID
            </label>
            <input
              id="route-plan-customer-id"
              value={planCustomerId}
              onChange={(event) => setPlanCustomerId(event.target.value)}
              className="mt-1 w-24 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
            />
          </div>
        )}
        <button
          type="submit"
          disabled={isPlanning}
          className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {isPlanning ? 'Planning…' : 'Plan route'}
        </button>
      </form>

      {planError && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          {planError}
        </p>
      )}

      {planResult && (
        <div className="mt-4">
          {planResult.unavailable ? (
            <p className="text-sm text-yellow-700 dark:text-yellow-400">
              {planResult.unavailable_message ?? 'Route data is currently unavailable.'}
            </p>
          ) : (
            <RouteMap routePlan={planResult} />
          )}
        </div>
      )}

      {isSupportAgent && (
        <div className="mt-6">
          <label
            htmlFor="route-filter-customer-id"
            className="block text-sm text-gray-600 dark:text-gray-300"
          >
            Filter by customer ID
          </label>
          <input
            id="route-filter-customer-id"
            value={filterCustomerId}
            onChange={(event) => setFilterCustomerId(event.target.value)}
            placeholder="All customers"
            className="mt-1 w-40 rounded border border-gray-300 px-2 py-1 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
          />
        </div>
      )}

      <h2 className="mt-8 text-lg font-semibold text-gray-900 dark:text-white">
        Today's routes
      </h2>
      {isLoadingList ? (
        <p className="mt-2 text-gray-600 dark:text-gray-300">Loading routes…</p>
      ) : listError ? (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          Failed to load routes: {listError}
        </p>
      ) : (
        <>
          <h3 className="mt-4 text-sm font-semibold uppercase text-gray-500 dark:text-gray-400">
            Active ({active.length})
          </h3>
          {active.length === 0 ? (
            <p className="mt-1 text-gray-600 dark:text-gray-300">No active routes today.</p>
          ) : (
            <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
              {active.map((route) => (
                <li
                  key={route.route_plan_id}
                  data-testid={`route-${route.route_plan_id}`}
                  className="px-4 py-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-900 dark:text-white">
                      {route.origin_label} → {route.destination_label}
                    </span>
                    <span
                      data-testid={`route-status-${route.route_plan_id}`}
                      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${STATUS_BADGE[route.status]}`}
                    >
                      {route.status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {route.warnings.length === 0
                      ? 'No warnings'
                      : `${route.warnings.length} warning(s)`}
                    {' · '}
                    {formatTime(route.created_at)}
                  </p>
                  <button
                    type="button"
                    onClick={() => void handleMarkComplete(route.route_plan_id)}
                    className="mt-2 rounded border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 dark:border-gray-600 dark:text-gray-200"
                  >
                    Mark complete
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3 className="mt-6 text-sm font-semibold uppercase text-gray-500 dark:text-gray-400">
            Completed ({completed.length})
          </h3>
          {completed.length === 0 ? (
            <p className="mt-1 text-gray-600 dark:text-gray-300">No completed routes today.</p>
          ) : (
            <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700 overflow-hidden rounded-lg bg-white dark:bg-gray-800 shadow">
              {completed.map((route) => (
                <li
                  key={route.route_plan_id}
                  data-testid={`route-${route.route_plan_id}`}
                  className="px-4 py-3"
                >
                  <span className="text-sm font-medium text-gray-900 dark:text-white">
                    {route.origin_label} → {route.destination_label}
                  </span>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {route.warnings.length === 0
                      ? 'No warnings'
                      : `${route.warnings.length} warning(s)`}
                    {' · completed '}
                    {formatTime(route.completed_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npm run test -- Routes.test.tsx`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full frontend test suite**

Run: `cd frontend && npm run test`
Expected: PASS — in particular confirm `RouteMap.test.tsx` and `ChatWidget.test.tsx` still pass (the `route_plan_id?` field was added as optional specifically so their existing fixtures keep compiling).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/routePlan.ts frontend/src/pages/Routes.tsx frontend/src/pages/Routes.test.tsx
git commit -m "Flesh out the Routes page into a route selector + daily tracker"
```

---

## Task 7: Documentation updates

**Files:**
- Modify: `docs/ROUTE_PLANNING.md`
- Modify: `docs/SECURITY.md`
- Modify: `docs/API_REFERENCE.md`
- Modify: `docs/DATA_MODEL.md`

**Interfaces:**
- Consumes: nothing (documentation only, describing Tasks 1-6's shipped behavior).

- [ ] **Step 1: Update `docs/DATA_MODEL.md`**

Add a new `### RoutePlan` subsection (mirroring the existing `### DrivingEvent` entry's table format) after the `KnowledgeBaseArticle` entry and before `AuditLog`, documenting: `route_plan_id`, `customer_id` (FK), `created_by_role`/`created_by_id`, `origin_label`/`destination_label`, `distance_km`/`duration_min`/`geometry`/`warnings` (JSON), `unavailable`/`unavailable_reason`, `status` (`active`/`completed`), `created_at`/`completed_at`. Note it is distinct from `AuditLog`'s `route_plan_generated` entries (structured/queryable vs. free-text compliance trail).

- [ ] **Step 2: Update `docs/SECURITY.md`**

In the existing "Risk-zone lookups are not tenant-scoped" section (or immediately after it), add a short paragraph clarifying the distinction this plan introduces: route *computation* (`build_route_plan`) remains untenanted (any authenticated user can plan a route anywhere), but route *persistence* (the new `RoutePlan` table) is customer-scoped exactly like `SupportTicket`/`Notification` — a `customer` caller's saved plans are always their own; a `support_agent` caller must name a `customer_id` to save one, and sees all customers' saved plans by default when listing.

- [ ] **Step 3: Update `docs/ROUTE_PLANNING.md`**

Add a new `## Daily route tracking` section covering: the `RoutePlan` table and `save_route_plan()` (called from both `POST /route-plan` and the chat route-plan intent, including on `unavailable` results), `GET /route-plans` (query params, scoping table matching `docs/API_REFERENCE.md`'s existing style), `PATCH /route-plans/{id}/complete` (manual-only completion, 404/409 cases), and the new chat "today's routes" intent (trigger phrases, deterministic/no-LLM summary, fleet-wide-for-support_agent behavior). Also update the existing "Chat integration" section's intent-order description to mention the new intent is checked between the route-plan intent and the report intent.

- [ ] **Step 4: Update `docs/API_REFERENCE.md`**

In the "Route Planning" section: add `route_plan_id` to the existing `POST /route-plan` response example; document the new `customer_id` request field (required for `support_agent`, mirroring the existing `POST /reports/*` documentation style already in this file); add `GET /route-plans` (query params, response shape, scoping table) and `PATCH /route-plans/{id}/complete` (response shape, 404/409) as new subsections.

- [ ] **Step 5: Commit**

```bash
git add docs/DATA_MODEL.md docs/SECURITY.md docs/ROUTE_PLANNING.md docs/API_REFERENCE.md
git commit -m "Document the route-selector + daily route/risk tracking feature"
```
