# Demo Route Data, Driver-Aware Overviews, and Opt-In Escalation Email

## Problem

Three related gaps, all raised together as one improvement request:

1. A customer asking "are there any problems with my route" or "give me a
   route overview" gets no useful answer today -- the chat intent that lists
   a customer's routes only fires on the words "today"/"active", and even
   when it fires there's rarely any `RoutePlan.warnings` data to show,
   because nothing populates warnings unless the real ORS/Open-Meteo risk
   pipeline happens to flag something on an actually-planned route.
2. A support agent asking "what's today's overview" or "give me the daily
   report" gets routes and warnings, but never who was driving --
   `RoutePlan.driver_id` (just merged from the live-tracking branch) isn't
   surfaced anywhere in chat/report output yet.
3. When the AI can't confidently answer, it silently creates a support
   ticket today -- there is no user-facing choice, and no way to notify a
   human outside the app (email).

**No real GPS/incident-detection hardware exists anywhere in this
codebase** (all telematics data is synthetic/seeded, consistent with the
rest of this POC). The "random problems"/"random active routes" called for
here are explicitly demo/test scaffolding, not a placeholder for a future
real-detection swap within this spec's scope -- the module that generates
them says so in its own docstring.

## Scope decisions from brainstorming

- Demo route data is generated once by a manual seed script and persists
  (not regenerated per-question) -- consistent, referenceable, and an
  escalation email can quote exactly what the customer saw.
- The admin "who was driving" view stays scoped to whichever customer the
  support agent is currently working with in that chat session (same
  `chat_session.customer_id`-derived scoping already used everywhere else
  in this codebase) -- **not** fleet-wide. This preserves the fix already
  documented in `docs/SECURITY.md` ("Fixed vulnerability: chat's 'today's
  routes' intent leaked other customers' data"): an unscoped/fleet-wide
  answer must never be persisted into one customer's `ChatSession`. A true
  fleet-wide view remains the existing non-chat `GET /route-plans` page,
  which is safe because it returns straight to the caller and is never
  written into a tenant-owned row.
- The low-confidence auto-escalation (`app/ai/escalation.py`) is replaced
  with an explicit opt-in: the customer is asked, and a ticket + email are
  only created if they say yes. Nothing happens silently.
- Escalation email uses real SMTP (`smtplib`), configured via new optional
  settings -- empty/unset by default, like `ors_api_key` already is. Real
  credentials must be supplied in `.env` for delivery to actually work.
- This branch is forked from `main` **after** merging the live-tracking
  branch, specifically so `RoutePlan.driver_id` and its Driver join are
  available.

## Data model

No schema changes. Everything here is generated data (via the new seed
script) plus formatting/routing changes over the existing `RoutePlan`
table (`driver_id`, `warnings`, `status`, `created_at`, `completed_at`)
and the existing `ChatMessage`/`SupportTicket`/`Notification` tables.

## Backend changes

### New module: `app/seed/seed_demo_routes.py`

A manually-run script (`docker compose exec backend python -m
app.seed.seed_demo_routes`), documented in `docs/DEPLOYMENT.md` alongside
the existing `python -m app.seed.seed` section. Its own module docstring
states plainly that this generates synthetic demo data for testing the
chat/report surfaces, not a stand-in for real incident detection.

Each run:

- Picks a handful of existing seeded customers (including whichever
  customer the operator is demoing as) and, for each, a few of that
  customer's existing `Driver` rows.
- Creates several `RoutePlan` rows dated "today" in Sydney-local time
  (via the existing `app.timeutil.site_today()`/`site_day_bounds()`), a mix
  of `status=ACTIVE` and `status=COMPLETED` (with `completed_at` set for
  the latter).
- `origin_label`/`destination_label` drawn from a small hand-picked list of
  real Sydney-area place-name pairs (e.g. "Sydney CBD" -> "Parramatta",
  "Bondi Beach" -> "Sydney Airport") -- no ORS geocoding/directions calls,
  so the script runs instantly with no network dependency or API key
  requirement.
- `geometry` is a plain 2-point GeoJSON `LineString` between fixed lat/lon
  pairs for those place names (enough for the live map/tracking page to
  render something real; not a real routed polyline).
- `distance_km`/`duration_min` are plausible fixed/randomized values per
  place pair, not computed.
- `driver_id` is randomly assigned from that customer's drivers, left
  `NULL` ("Unassigned") on a minority of rows to match real usage.
- `warnings` is a randomly-sized (0-3) list drawn from a small pool of
  realistic-sounding synthetic entries, in the exact shape
  `app.ai.route_planning.Warning` already produces (`type: "weather" |
  "risk_zone"`, `severity: "moderate" | "high"`, `description`) -- so every
  existing consumer (reports, chat, the live map's marker popups) treats
  them identically to real risk-pipeline output, with no format branching
  anywhere else in the codebase.
- `created_by_role="support_agent"`, `created_by_id` set to a seeded
  support agent's id (simulating dispatch), matching the existing
  `RoutePlan.created_by_*` columns' meaning.

Idempotent-ish by convention, not by constraint: re-running the script adds
another batch rather than erroring, since "today" may span multiple demo
runs. The script prints a summary (rows created per customer/status) when
it finishes.

### Wider "today's routes" intent (`app/api/chat.py`)

`_TODAYS_ROUTES_TEMPORAL_STATUS_WORDS` gains `"problem"`, `"problems"`,
`"issue"`, `"issues"`, `"overview"` alongside the existing `"today"`/
`"active"`. `_detect_todays_routes_intent` still requires a route word
(`"route"`/`"routes"`) AND one of these signal words, unchanged logic --
only the word list grows. Regression tests confirm this doesn't hijack
unrelated RAG questions that happen to contain "problem"/"issue" (mirroring
the existing regression tests for the "reported"/"risk" false-positive
fixes already in `backend/tests/test_chat_api.py`).

### Driver names in existing output

Two functions already loop over today's `RoutePlan` rows and just need a
`Driver` join added:

- `app.ai.route_planning.summarize_todays_routes` -- each line gains the
  driver's `full_name` (or "Unassigned"), e.g. `"- Sydney CBD -> Parramatta
  (active, driver: Alice Driver): 2 warning(s), 1 high-severity"`.
- `app.ai.reports._format_route_risk_warnings` (used by both
  `generate_start_of_day_report` and `generate_end_of_day_report`) -- same
  addition to its per-route line.

Both already have `db`/`Session` in scope; the join is a single extra
query (`db.query(Driver).filter(Driver.driver_id.in_(...))`, batched once
per call, not per-row) added alongside the existing per-row formatting,
matching how `reports.py`'s other formatters already batch-fetch
`drivers_by_id` once and look up by id per row.

### Opt-in escalation

`app.ai.escalation.handle_answer` no longer creates a `SupportTicket`/
`Notification` itself. On a low-confidence answer it now only returns
`EscalationResult(text=FALLBACK_TEXT, escalated=False,
escalation_offered=True, support_ticket_id=None)` -- a new
`escalation_offered: bool` field on the existing dataclass.
`ChatResponse` (`app/api/chat.py`) gains the matching
`escalation_offered: bool` field. `escalated` keeps its existing meaning
("a ticket now exists for this exchange") but is only ever set `True` by
the new explicit-confirm endpoint below, never implicitly by
`handle_answer` itself.

New endpoint: `POST /chat/messages/{chat_message_id}/escalate`, same file,
mirroring `submit_message_feedback`'s exact ownership-check shape
(`db.get(ChatMessage, ...)`, 404 if missing, 404 -- not 403 -- if the
resolved `ChatSession.customer_id` isn't the caller's for a `customer`
role, 400 if the message isn't `role=ASSISTANT`). On success:

1. Reuses `app.ai.escalation`'s existing idempotent
   `_get_or_create_escalation_ticket` select-first/`IntegrityError`-savepoint
   pattern (renamed/exported if needed) so a double-click on "Yes" cannot
   create two tickets for the same exchange.
2. Creates the ticket + an in-app `Notification`
   (`notification_type=PreferredNotificationMethod.IN_APP`, unchanged from
   today) inside the request's transaction, committed once.
3. **After** that commit succeeds, best-effort sends the actual external
   email (see below) -- wrapped in its own `try/except`, never rolling back
   the already-committed ticket if email delivery fails.
4. Returns `{support_ticket_id: int, email_sent: bool}`.

### New module: `app/integrations/email.py`

Mirrors `app/integrations/openrouteservice.py`'s shape (a thin wrapper
module, no FastAPI/DB imports): `send_escalation_email(*, customer_name:
str, customer_email: str, question: str, answer: str, support_ticket_id:
int) -> None`, using the stdlib `smtplib` + `email.message.EmailMessage`
(no new dependency). Raises on failure -- the caller (the new endpoint)
decides what to do with that, matching how `openrouteservice.py`'s own
functions raise and callers translate failures into user-facing text
rather than swallowing them internally.

New `app.config.Settings` fields, all optional and empty-by-default like
`ors_api_key`:

```python
smtp_host: str = ""
smtp_port: int = 587
smtp_username: str = ""
smtp_password: str = ""
smtp_from_address: str = ""
smtp_use_tls: bool = True
escalation_email_to: str = "CIHE241731@student.edu.cihe.au"
```

`send_escalation_email` treats an empty `smtp_host` as "not configured"
and raises immediately (same convention `openrouteservice.py` uses for its
own empty-API-key case) rather than attempting a connection that can only
fail. Never logs the SMTP password. The email body includes: the
customer's full name, the customer's own question, the bot's fallback
answer text, and the support ticket's id as a reference number.

## Frontend changes

### `ChatWidget.tsx`: escalation offer

When an assistant message's `escalation_offered` is `true`, render an
inline prompt on that message -- same placement/styling family as the
existing thumbs-up/down feedback buttons, not a new modal:

> "Would you like to escalate this to a human? **[Yes]** **[No]**"

- **No** -- purely local state, dismisses the prompt, no request sent.
- **Yes** -- calls `POST /chat/messages/{message_id}/escalate`, then
  replaces the prompt with a confirmation: "A support agent has been
  notified (ref #`<support_ticket_id>`)" when `email_sent` is `true`, or
  the same message with an added "(email notification could not be sent)"
  when `false` -- the ticket exists either way, so the customer is never
  told escalation failed when it didn't.

No changes needed to how `escalated` was previously rendered elsewhere,
since nothing sets it `True` on the initial answer anymore.

## Error handling

- Seed script: skips a customer with no drivers rather than crashing the
  whole run; prints what it skipped.
- Widened intent keywords: guarded by the existing regression-test
  discipline (this session has fixed two prior false-positive collisions
  the same way) rather than a runtime guard -- there's no runtime
  ambiguity to catch, only a test-time one.
- Escalate endpoint: ticket creation and email sending are two separate
  failure domains by design (see above) -- a down/misconfigured SMTP
  server degrades to "ticket created, email not sent," never to "nothing
  happened" or a 500.
- `send_escalation_email`: a real `smtplib` failure (auth, connection,
  timeout) propagates as a plain exception with the underlying error
  message preserved, for the endpoint to log and translate into
  `email_sent=false` -- never retried automatically (no background job
  infrastructure exists in this app to retry against, consistent with the
  rest of the codebase).

## Testing

- Backend: `_detect_todays_routes_intent` widened-keyword tests, including
  a regression test that a genuine unrelated question containing
  "problem"/"issue" doesn't get hijacked (mirrors
  `test_chat_api.py`'s existing false-positive regression tests). Driver-
  name-in-output tests for both `summarize_todays_routes` and
  `_format_route_risk_warnings` (assigned + unassigned cases). Tests for
  `POST /chat/messages/{id}/escalate`: ownership/404 (mirrors the existing
  feedback-endpoint tests), double-click idempotency (one ticket, not
  two), ticket-created-even-when-email-raises. A mocked-SMTP test for
  `send_escalation_email` (patches `smtplib.SMTP`, no real network --
  same philosophy as the existing ORS/Open-Meteo `httpx` mocking) plus an
  unconfigured-host-raises-immediately test. A smoke test for
  `seed_demo_routes` confirming every row it creates round-trips through
  the real `RoutePlan` model/validators.
- Frontend: a new `ChatWidget.test.tsx` case for the Yes/No escalation
  prompt (renders on `escalation_offered`, Yes calls the endpoint and
  shows the ref-number confirmation, No dismisses with no request sent).

## Out of scope

- Real risk/incident detection (still simulated/random, as requested).
- Scheduled/recurring demo-data regeneration -- manual script run only.
- Email delivery status tracking beyond a single `email_sent` boolean (no
  bounce handling, no retry queue).
- Changing the existing per-customer chat scoping model, or building a
  fleet-wide chat/report answer -- the existing `GET /route-plans` page
  remains the fleet-wide surface.
- Customer-configurable escalation destination -- the target address is
  one operator-configured setting, not per-customer.
