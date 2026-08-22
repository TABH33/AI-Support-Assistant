# Security

## Authentication

Local, password-based auth against the `Customer` / `SupportAgent` tables —
no OAuth or third-party identity provider (deliberate, POC-scale scope).

- **Passwords**: hashed with `bcrypt` (`backend/app/auth/security.py`),
  never stored or logged in plaintext.
- **Tokens**: JWTs signed with HS256 using `JWT_SECRET` (env var, never
  hardcoded), carrying `sub` (user id), `role` (`customer` /
  `support_agent`), and `access_level` (support agents only). Default
  expiry: 60 minutes.
- **`POST /auth/login`** checks `Customer` first, then `SupportAgent`, by
  email. Every failure mode — unknown email on both tables, or a matching
  email with the wrong password — returns the identical generic `401`
  ("Incorrect email or password"), so the API never reveals whether an
  email is registered.
- There is **no signup endpoint** anywhere in this codebase. Accounts are
  created only via the seed script or direct DB access — see
  [DEPLOYMENT.md](DEPLOYMENT.md) for how the deployed instance's login
  credentials were actually set.

## Authorization (RBAC)

`app/auth/dependencies.py`'s `require_role(*roles)` gates almost every
route: `Depends(require_role("customer", "support_agent"))`. It:

- Returns `401` if the token is missing, malformed, unsigned/wrongly
  signed, or expired.
- Returns `403` if the token is valid but its `role` claim isn't in the
  allowed set for that route.

Every telematics/chat/report/ticket/notification endpoint requires this
dependency; only `POST /auth/login` and `GET /health` are open.

## Multi-tenant isolation

This is the single security property enforced most heavily across the
codebase: **a `customer`-role caller can only ever see or affect their own
fleet's data**, and this is enforced by deriving `customer_id` from the
verified JWT — never from anything client-supplied (request body or query
string) — at every data-access point.

- `app/api/telematics.py`'s `_scoped_*` helpers filter every query by
  `current_user.user_id` for a customer caller; a `support_agent` caller is
  unrestricted (they need visibility across every customer to help any of
  them), optionally narrowed by an explicit `?customer_id=` filter.
- `POST /chat`'s security-critical detail: `driver_id`/`trip_id`/
  `vehicle_id` in the request body are client-supplied context hints, but
  the `customer_id` actually used for the `TelematicsDataSource` lookups
  always comes from the resolved `ChatSession.customer_id` — reusing an
  existing session requires it to already belong to the caller, and
  creating a new session as a `customer` always uses
  `current_user.user_id`, never a request-body `customer_id`. A malicious
  or buggy client cannot get another customer's telematics data reflected
  into their chat answer by guessing ids.
- **A resource that exists but belongs to a different customer returns
  `404`, not `403`**, everywhere in this API — a customer can never
  distinguish "doesn't exist" from "exists but isn't yours."
- At the data-access layer itself, `TelematicsDataSource`
  (`app/datasources/base.py`) makes `customer_id` a **required, keyword-only
  parameter with no default** on every per-id lookup method
  (`get_driver`/`get_vehicle`/`get_trip`/`get_trips_for_driver`/
  `get_driving_events`) — omitting it is a `TypeError` at the call site, not
  a silently-unscoped query. This was a deliberate fail-closed fix: an
  earlier version defaulted `customer_id` to `None` (meaning "unscoped"),
  which would have let a future caller accidentally skip tenant filtering
  just by forgetting the argument.

## At-rest PII encryption

`Customer.full_name`, `Customer.email`, and `Customer.phone_number` are
encrypted at rest (`backend/app/security/crypto.py`), using two modes
depending on whether the column needs to remain a working equality-lookup
target:

- **`full_name` / `phone_number`** — randomized **AES-256-GCM**, fresh
  96-bit nonce per write (`os.urandom`, never fixed/derived). Nothing in
  this codebase queries either column by equality, so there's no reason to
  accept any determinism tradeoff for them.
- **`email`** — deterministic **AES-SIV** (RFC 5297). Chosen specifically
  because `POST /auth/login` does `Customer.email == credentials.email`: a
  randomized cipher would produce different ciphertext every write, and
  that `==` lookup would never match an existing row again after the
  first write. AES-SIV's synthetic IV is derived from the plaintext itself
  (via CMAC), so identical plaintext always yields identical ciphertext —
  and unlike a hand-rolled "reuse a fixed AES-GCM nonce" scheme, it's
  purpose-built to be safe without a caller-supplied nonce.
  **Accepted tradeoff**: deterministic ciphertext leaks whether two rows
  share the same email (equal ciphertext), though never the plaintext
  itself — standard for any encrypted column that must remain a working
  `=` lookup target without a separate blind-index column.

Both modes derive independent subkeys via HKDF-SHA256 (distinct `info`
labels) from one `ENCRYPTION_KEY` secret (base64, ≥32 raw bytes) — the only
encryption secret an operator manages. **Losing or rotating this key without
a re-encryption migration makes every existing encrypted row permanently
undecryptable** — back it up like any other production secret.

`Driver.email`/`Driver.phone_number` and `SupportAgent.email` are **not**
encrypted — only `Customer`'s PII columns are in scope for this control.

## Audit logging

Every AI-generated recommendation shown to a user is recorded in
`audit_logs` (`app/security/audit.py`):

- `action=chat_answer` — one row per `POST /chat` turn, noting confidence
  and whether it was escalated.
- `action=report_generated` — one row per generated report (chat-routed or
  via the direct `/reports/*` endpoints), noting the report type.

`action` is a plain string, not a DB-level enum, so a future auditable
action (e.g. a not-yet-built support-agent ticket-status-change feature)
can start writing new values without a schema migration. Audit rows are
written via `db.flush()` (not their own commit) so they land atomically
together with the request they describe — see [RAG_PIPELINE.md](RAG_PIPELINE.md#atomicity).

## HTTPS / TLS

Out of scope for this repository by design. The Docker Compose stack runs
every service over plain HTTP on the Docker bridge network. TLS termination
is expected to be handled by a reverse proxy (nginx, Traefik, a cloud load
balancer) sitting in front of the `backend`/`frontend` containers in any
real deployment — see `README.md`'s "Security & Compliance" section for the
full reasoning. Secrets that matter regardless of transport (`JWT_SECRET`,
`ENCRYPTION_KEY`, `POSTGRES_PASSWORD`) are still never hardcoded and are
always sourced from `.env`.
