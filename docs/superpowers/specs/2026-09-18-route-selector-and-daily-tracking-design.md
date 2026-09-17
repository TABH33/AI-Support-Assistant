# Route Selector + Daily Route/Risk Tracking

## Problem

The route-planning pipeline (`POST /route-plan`, and the chat route-plan
intent) merged into `main` in this same session computes a route plan and
returns it -- it persists nothing. There is no page for a customer or a
manager (`support_agent`) to plan and browse routes, and no way for the
chatbot to answer a question like "what routes were used today, any risk
signals?" -- there is nothing in the database to query for that.

This spec adds: a dedicated route-selector UI (fleshing out the existing
`frontend/src/pages/Routes.tsx` placeholder), persistence of every planned
route for the day it was created, and a new chat capability that reports on
today's routes.

## Data model

New table `route_plans`, new model `RoutePlan`
(`backend/app/models/route_plan.py`):

| Field | Type | Notes |
|---|---|---|
| `route_plan_id` | int, PK | |
| `customer_id` | int, FK -> `customers.customer_id`, not null | Derived from JWT for a `customer` caller; required explicitly for a `support_agent` caller -- same rule as `ReportRequest.customer_id` |
| `created_by_role` | string(32) | `"customer"` / `"support_agent"` |
| `created_by_id` | int | the JWT `user_id` of whoever submitted it |
| `origin_label` | string(255) | display text as submitted (place name or `"{lat},{lon}"`) |
| `destination_label` | string(255) | |
| `distance_km` | numeric(10,2), nullable | copied from `RoutePlanResult` |
| `duration_min` | numeric(10,2), nullable | |
| `geometry` | JSON, nullable | GeoJSON LineString, as returned by `build_route_plan` |
| `warnings` | JSON, not null, default `[]` | list of `{location, distance_from_origin_km, type, severity, description}` |
| `unavailable` | bool, not null | |
| `unavailable_reason` | string(32), nullable | |
| `status` | enum `route_plan_status`: `active` / `completed`, default `active` | |
| `created_at` | timestamptz, not null | |
| `completed_at` | timestamptz, nullable | |

A new Alembic migration adds this table. No change to `DrivingEvent` or any
other existing table.

**Why a new table instead of reusing `AuditLog`**: `AuditLog.description` is
a free-text string (`f"origin={...} destination={...} unavailable=..."`,
see `backend/app/api/route_plan.py`) -- adequate for a compliance trail, not
for structured queries like "today's active routes with a high-severity
warning." Audit logging of route-plan creation continues unchanged
alongside this new table; they serve different purposes.

## Backend changes

### `POST /route-plan` (existing endpoint, modified)

- `RoutePlanRequest` gains an optional `customer_id: int | None` field,
  handled exactly like `ReportRequest.customer_id`: ignored (forced to
  `current_user.user_id`) for a `customer` caller; required (400 if
  omitted) for a `support_agent` caller.
- After `build_route_plan(...)` returns, the endpoint inserts a `RoutePlan`
  row capturing the result (including `unavailable` cases -- a failed plan
  is still worth recording, so "the route service was down at 2pm" is
  answerable later).
- `RoutePlanResponse` gains `route_plan_id: int`.
- Existing `record_audit_event(action=ACTION_ROUTE_PLAN_GENERATED, ...)`
  call is unchanged.

This is a real scope change to an endpoint whose own docstring currently
says "no `customer_id` scoping is needed for this endpoint's own logic" --
`docs/SECURITY.md`'s "risk-zone lookups are not tenant-scoped" section and
`docs/ROUTE_PLANNING.md` get updated to reflect that route *computation*
remains untenanted (any authenticated user can plan a route anywhere) while
route *persistence* is now customer-scoped (whose record this becomes).

### `GET /route-plans`

New endpoint. Query params: `date` (defaults to today, server timezone
UTC), `status` (`active` / `completed`, optional), `customer_id` (optional,
`support_agent` only, mirrors `GET /tickets?customer_id=`). Scoping:
`customer` caller sees only their own rows; `support_agent` sees all rows
for the given `date`/`status`, optionally narrowed by `customer_id`.
Returns a list of the same shape as `RoutePlanResponse` plus `status`,
`created_at`, `completed_at`, `customer_id`.

### `PATCH /route-plans/{route_plan_id}/complete`

New endpoint. Sets `status=completed`, `completed_at=now()`. Allowed for
the owning customer (`RoutePlan.customer_id == current_user.user_id`) or
any `support_agent`. 404 if the row doesn't exist or (for a `customer`
caller) isn't theirs -- same "don't leak existence" rule as every other
customer-scoped resource. 409 if already completed (idempotency: calling
twice is a real possibility from a slow UI double-click; reject the second
call rather than silently no-op, since a stale `completed_at` overwrite
would be a real if minor bug).

### Chat: new "today's routes" intent

A new keyword/regex check in `app/api/chat.py`, alongside
`_detect_route_plan_intent` and `_detect_report_intent`, checked in this
order: route-plan intent (plan a *new* route) -> **today's-routes intent**
(new) -> report intent -> RAG fallback. It needs to come before the report
check because a phrase like "route report" would otherwise be swallowed by
the existing (already-documented-as-buggy) substring match in
`_detect_report_intent`.

Trigger phrases (illustrative, not exhaustive): "routes today", "today's
routes", "active routes", "route risk(s) today", "risk signals for the
routes". Implemented as a small keyword set requiring both a route-word
(`route`/`routes`) and a temporal/status word (`today`/`active`/`risk`), to
avoid colliding with the existing route-*planning* intent's
"from X to Y" pattern.

On match: query `RoutePlan` for today, scoped by `chat_session.customer_id`
for a `customer` caller or all customers for a `support_agent` caller (per
your answer: unscoped manager query = fleet-wide, matching every other
support_agent-facing list endpoint). Formats a **deterministic, non-LLM**
text summary -- route count by status, each route's origin/destination,
distance, and any warnings (highest severity first). No LLM call: the
existing RAG/report paths already establish "never let the assistant guess
or reword the actual numbers," and an LLM rewrite of a warning count or
severity is exactly the kind of drift that principle exists to prevent.
Returns `confidence: 1.0, escalated: false`, bypassing RAG entirely, same
as the report and route-planning intents.

## Frontend changes

`frontend/src/pages/Routes.tsx` (currently a placeholder) becomes:

1. A form (origin, destination) reusing the existing `RouteMap` component,
   submitting to `POST /route-plan` (now customer-scoped) and rendering the
   result the same way `ChatWidget` already does.
2. Below it, two lists fetched from `GET /route-plans?date=today`: **Active**
   and **Completed**, each row showing origin -> destination, distance/
   duration, and warning badges (reusing `WarningOut`'s `severity`/
   `description`). Active rows get a "Mark complete" button (`PATCH
   .../complete`).
3. A `support_agent` caller additionally sees a customer filter (reusing
   the same `?customer_id=` pattern already used on `Alerts`/`Drivers`
   pages, if one exists there -- otherwise a simple dropdown populated from
   `GET /devices` or an equivalent existing customer-listing call).

No new frontend route/page is added -- `Routes.tsx` is already wired into
`App.tsx`'s protected shell.

## Testing

- Backend: pytest for the model/migration, `POST /route-plan`'s new
  persistence + customer_id handling, `GET /route-plans` scoping (customer
  sees only own, support_agent sees all/filtered), `PATCH .../complete`
  (success, 404 wrong-owner, 409 double-complete), and the new chat intent
  (detection regex + the deterministic summary's correctness against a
  seeded `RoutePlan` fixture).
- Frontend: Vitest for the new list/complete-button behavior in
  `Routes.tsx`, mirroring the existing `RouteMap.test.tsx`/
  `ChatWidget.test.tsx` patterns.

## Out of scope (explicitly, to keep this bounded)

- No automatic expiry/completion (rejected in favor of the manual button).
- No editing of a saved route plan after creation (re-plan and let the old
  one age out as `active` -> manually completed, rather than mutating
  `geometry`/`warnings` in place).
- No pagination on `GET /route-plans` -- a single day's route volume for
  this POC's seeded fleet size doesn't need it; noted as a known limitation
  if real usage ever needs it.
