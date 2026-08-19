# Plan: Ctrack AI-Driven Telematics Support Assistant

**Source**: ICT307 project reports (Proposal, Requirement Analysis, ASS3 System Architecture) at repo root.
**Complexity**: Large

## Summary
Build a fleet-management web dashboard with an embedded ("native") floating AI chat widget, backed by a RAG engine over a local LLM (Ollama), a Postgres+pgvector store, and an escalation workflow to human support — per the architecture, database schema, UI design, and compliance requirements already specified in the three source reports. Real Ctrack/Databricks data is not yet available: build against a synthetic-data source behind a swappable interface so a Databricks connector can replace it later without touching RAG/escalation logic.

## Global Constraints
(Copy these verbatim into every task dispatch's context — they bind every task.)

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.x + Alembic for migrations, pydantic-settings for config (no hardcoded secrets — everything through `.env` / `.env.example`).
- **Database**: PostgreSQL 15+ with the `pgvector` extension enabled. Schema/entity names and fields MUST match the source reports exactly: `Customer`, `Device`, `Driver`, `Vehicle`, `Trip`, `DrivingEvent`, `ChatSession`, `SupportTicket`, `Notification`, `SupportAgent`, `KnowledgeBaseArticle`. Field names as listed in ASS3 §3 (e.g. `CustomerID`, `FullName`, `PreferredNotificationMethod`, `AIConfidenceScore`, `SessionStatus`, `TicketStatus`, `Priority`, `AssignedSupportAgent`, `DeviceStatus`, etc.) — use `snake_case` Python/SQL equivalents of these exact concepts (e.g. `customer_id`, `full_name`, `ai_confidence_score`) rather than inventing new field concepts.
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS, `react-router` for routing.
- **LLM**: local via Ollama. Default chat model `llama3.1:8b-instruct`, configurable via `OLLAMA_MODEL` env var. Default embedding model `nomic-embed-text`, configurable via `OLLAMA_EMBED_MODEL` env var. All LLM/embedding calls go through a single service module — never call Ollama directly from route handlers.
- **RBAC**: every backend endpoint that touches customer, device, chat, or ticket data enforces role checks via a shared FastAPI dependency (`require_role(...)`). Roles: `customer`, `support_agent` (with `AccessLevel` tiers per ASS3 §3.6).
- **Data privacy**: all seed/test data is clearly fictional (no real names, emails, or PII). PII-bearing columns (`Email`, `PhoneNumber`, `FullName`) are candidates for at-rest encryption — implemented in the security-hardening task, not before.
- **Testing**: backend uses `pytest` (unit + integration against a test Postgres DB or SQLite fallback where practical); frontend uses `vitest` + React Testing Library. Every task's Definition of Done includes passing tests for the code it adds.
- **Docker**: all services (`postgres` w/ pgvector image, `ollama`, `backend`, `frontend`) run via `docker-compose.yml` at repo root, single `docker compose up` brings up the full stack.
- **No task invents a new architectural layer, data store, or LLM provider not listed above.** If a task's implementer believes one is needed, it must stop and report `NEEDS_CONTEXT` rather than deciding unilaterally.

## Patterns to Mirror
No existing code in this repository — only the three source `.docx` reports. There is nothing to mirror; naming and structure below are derived directly from the entity names, roles, and workflows already committed to in those reports (see Global Constraints for the exact names).

## Files to Change
| File/Dir | Action | Why |
|---|---|---|
| `backend/` | CREATE | FastAPI app, models, services, tests |
| `frontend/` | CREATE | React dashboard + chat widget |
| `docker-compose.yml` | CREATE | Local dev orchestration |
| `.env.example` | CREATE | Documented config surface |
| `README.md` | CREATE | Setup instructions |

## Tasks

### Task 1: Backend scaffolding
- **Action**: Create `backend/` as a FastAPI project: `backend/app/main.py` with a `/health` endpoint returning `{"status": "ok"}`, `backend/app/config.py` using `pydantic-settings` (`BaseSettings`) reading `DATABASE_URL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_EMBED_MODEL`, `JWT_SECRET`, `SESSION_TIMEOUT_MINUTES` from env. Add `backend/pyproject.toml` (or `requirements.txt`) pinning `fastapi`, `uvicorn`, `sqlalchemy`, `alembic`, `pydantic-settings`, `psycopg[binary]`, `pytest`, `httpx` (for TestClient). Add `backend/Dockerfile`. Add `backend/tests/test_health.py` asserting the `/health` endpoint.
- **Mirror**: N/A — first backend code in the repo; establishes the pattern later tasks follow (`app/` package, `tests/` mirrors `app/` structure).
- **Validate**: `cd backend && pytest` passes; `uvicorn app.main:app` boots and `/health` returns 200.

### Task 2: Frontend scaffolding
- **Action**: Create `frontend/` as a Vite + React + TypeScript app with Tailwind configured. Add a minimal `App.tsx` rendering a placeholder landing page and a `frontend/src/lib/apiClient.ts` stub (fetch wrapper reading `VITE_API_BASE_URL`). Add `frontend/Dockerfile`. Add one `vitest` smoke test (`App.test.tsx`) asserting the placeholder renders.
- **Mirror**: N/A — first frontend code.
- **Validate**: `cd frontend && npm run build` succeeds; `npm run test` passes.

### Task 3: Docker Compose & dev environment
- **Action**: Create root `docker-compose.yml` wiring: `postgres` (use an image with `pgvector` preinstalled, e.g. `pgvector/pgvector:pg16`), `ollama` (official `ollama/ollama` image, volume for model cache), `backend` (builds `backend/Dockerfile`, depends_on postgres+ollama, env from `.env`), `frontend` (builds `frontend/Dockerfile`, depends_on backend). Create root `.env.example` documenting every var from Task 1's config plus `POSTGRES_USER/PASSWORD/DB`. Create root `README.md` with setup steps (`cp .env.example .env`, `docker compose up`, how to pull the Ollama models, how to run backend/frontend tests locally).
- **Mirror**: Reuses env var names defined in Task 1's `config.py` — must match exactly, not invent parallel names.
- **Validate**: `docker compose config` validates without error (does not require actually pulling images/starting services in the sandbox — note this limitation in the report).

### Task 4: Database schema & migrations
- **Action**: In `backend/app/models/`, define SQLAlchemy 2.x models for `Customer`, `Device`, `Driver`, `Vehicle`, `Trip`, `DrivingEvent`, `ChatSession`, `SupportTicket`, `Notification`, `SupportAgent`, `KnowledgeBaseArticle` with fields and relationships exactly as specified in the Global Constraints and the ER relationships from ASS3 §3 (`Customer 1→Many Device`, `Customer 1→Many ChatSession`, `Customer 1→Many SupportTicket`, `Device 1→Many ChatSession`, `ChatSession 1→0..1 SupportTicket`, `SupportTicket 1→Many Notification`, `SupportAgent 1→Many SupportTicket`; plus `Driver 1→Many Trip`, `Trip →Vehicle`, `Trip 1→Many DrivingEvent` from the ASS2 class diagram). Add a `pgvector` `Vector` column (dimension 768, matching `nomic-embed-text`) on `KnowledgeBaseArticle` for its embedding. Wire up Alembic (`backend/alembic/`), generate the initial migration. Add `backend/tests/test_models.py` verifying each model can be instantiated and the foreign keys resolve against an in-memory/test DB.
- **Mirror**: Field/entity names per Global Constraints — this task IS the source of truth other tasks read from; do not deviate from the report's naming.
- **Validate**: `alembic upgrade head` runs clean against a test Postgres instance (or documents the SQLite limitation for pgvector if a real Postgres isn't available in the sandbox — report this explicitly); `pytest backend/tests/test_models.py` passes.

### Task 5: Synthetic data generator & seed script
- **Action**: Create `backend/app/seed/generator.py` producing plausible fictional data: ~10 customers, ~20 devices, ~15 drivers, ~15 vehicles, ~50 trips, ~200 driving events (mix of `speeding`, `harsh_braking`, `idling`, `route_deviation`), ~10 `KnowledgeBaseArticle` entries (troubleshooting guides/FAQs covering device pairing, harsh-braking alerts, GPS signal loss — matching the scenarios in ASS3 §4.2), and 2-3 `SupportAgent` records. Create `backend/app/seed/seed.py` as a runnable script/CLI (`python -m app.seed.seed`) that inserts this data via the Task 4 models. All generated names/emails must be obviously fictional (e.g. `driver-01@example.test`). Add `backend/tests/test_seed.py` verifying the generator produces referentially-consistent data (every FK resolves) without hitting a real DB (build the objects in memory and check invariants).
- **Mirror**: Uses Task 4's models directly — field names must match, no shadow schema.
- **Validate**: `pytest backend/tests/test_seed.py` passes; running the seed script against a local test DB populates all tables with no FK violations.

### Task 6: Auth & RBAC
- **Action**: Implement `backend/app/auth/` — password hashing (`passlib`/`bcrypt`), JWT issuance/verification (`python-jose` or `pyjwt`), `POST /auth/login` endpoint (accepts customer or support_agent credentials, returns a JWT carrying `role` and `access_level` claims), and a `require_role(*roles)` FastAPI dependency usable on any route. Add `backend/tests/test_auth.py` covering: successful login, wrong password rejected, protected route rejects missing/invalid token, `require_role` rejects a role not in the allowed set.
- **Mirror**: Uses `Customer`/`SupportAgent` models from Task 4.
- **Validate**: `pytest backend/tests/test_auth.py` passes.

### Task 7: Core CRUD APIs for telematics data
- **Action**: Implement read-focused REST endpoints under `backend/app/api/telematics.py`: `GET /drivers`, `GET /drivers/{id}`, `GET /vehicles`, `GET /trips` (filterable by driver/vehicle), `GET /trips/{id}/events`, `GET /devices` (filterable by customer). All routes protected by `require_role("customer", "support_agent")` from Task 6, and customer-role callers only see their own customer's data (filter by `customer_id` derived from the JWT). Add `backend/tests/test_telematics_api.py` covering: happy path listing/filtering, a customer cannot see another customer's devices, unauthenticated request is rejected.
- **Mirror**: Task 6's `require_role` dependency; Task 4's models.
- **Validate**: `pytest backend/tests/test_telematics_api.py` passes.

### Task 8: Chat/ticket/notification data-access layer
- **Action**: Implement `backend/app/repositories/chat.py` with functions: `create_chat_session(...)`, `end_chat_session(...)`, `create_support_ticket(chat_session_id, ...)`, `create_notification(ticket_id, customer_id, notification_type, ...)`. These are pure data-access functions (no AI logic yet — that's Task 12-14). Add `backend/tests/test_chat_repository.py` verifying each function persists correctly and the `ChatSession 1→0..1 SupportTicket` and `SupportTicket 1→Many Notification` relationships hold.
- **Mirror**: Task 4's `ChatSession`/`SupportTicket`/`Notification` models.
- **Validate**: `pytest backend/tests/test_chat_repository.py` passes.

### Task 9: Session retention job
- **Action**: Implement `backend/app/jobs/retention.py`: a function `purge_expired_sessions(db, timeout_minutes)` that deletes/anonymizes `ChatSession` rows (and their messages, once Task 15 adds a messages table — for now, just the session row and any directly-owned data) past `SESSION_TIMEOUT_MINUTES` since `EndTime` (or since `StartTime` if never ended). Expose it as a runnable script (`python -m app.jobs.retention`) so it can be cron-scheduled outside the app process. Add `backend/tests/test_retention.py` covering: expired session is purged, active session is untouched, boundary case at exactly the timeout.
- **Mirror**: Task 4's `ChatSession` model.
- **Validate**: `pytest backend/tests/test_retention.py` passes.

### Task 10: DataSource abstraction
- **Action**: Define `backend/app/datasources/base.py` with a `TelematicsDataSource` protocol/ABC exposing the read operations the RAG engine and dashboard need (e.g. `get_driving_events(trip_id)`, `get_trip(trip_id)`, `get_knowledge_base_articles()`). Implement `backend/app/datasources/synthetic.py` as `SyntheticDataSource(TelematicsDataSource)` backed by the Postgres tables from Task 4/5 (i.e., it just queries the DB — the abstraction is the interface, not a different storage). Wire the app's dependency injection so all later AI-layer code (Tasks 11-14) depends on `TelematicsDataSource`, never on SQLAlchemy models directly. Add `backend/tests/test_datasource.py` verifying `SyntheticDataSource` satisfies the protocol and returns expected shapes against seeded data.
- **Mirror**: Wraps Task 4's models/Task 7's query patterns behind an interface — this is the explicit seam for a future Databricks-backed implementation (not built in this plan).
- **Validate**: `pytest backend/tests/test_datasource.py` passes.

### Task 11: Knowledge-base embedding pipeline
- **Action**: Implement `backend/app/ai/embeddings.py`: a thin client wrapping calls to the Ollama embeddings endpoint (`OLLAMA_BASE_URL`/`OLLAMA_EMBED_MODEL`) returning a 768-dim vector for a given text. Implement `backend/app/ai/index_kb.py` as a runnable script that reads all `KnowledgeBaseArticle` rows (via `TelematicsDataSource` from Task 10), embeds their content, and writes the vector into the `pgvector` column from Task 4. Add `backend/tests/test_embeddings.py` that mocks the Ollama HTTP call (do not require a live Ollama instance in tests) and verifies the embedding client's request/response handling and the indexing script's DB write logic.
- **Mirror**: Task 10's `TelematicsDataSource`; Task 4's `KnowledgeBaseArticle.embedding` column.
- **Validate**: `pytest backend/tests/test_embeddings.py` passes (mocked Ollama, no live network/model dependency).

### Task 12: RAG retrieval service
- **Action**: Implement `backend/app/ai/retrieval.py`: `retrieve_context(query, driver_id=None, trip_id=None, vehicle_id=None, top_k=3)` that (a) embeds the query via Task 11's client, (b) runs a `pgvector` cosine-similarity query against `KnowledgeBaseArticle.embedding` for the top-k articles, (c) pulls relevant `DrivingEvent`/`Trip` rows for the given context ids via `TelematicsDataSource`, and (d) returns a structured `RetrievedContext` object combining both. Add `backend/tests/test_retrieval.py` (mocked embedding client + seeded/fake data) verifying the right KB article surfaces for a query like "harsh braking alert" and that trip context is correctly attached when a `trip_id` is passed.
- **Mirror**: Task 10's `TelematicsDataSource`, Task 11's embedding client.
- **Validate**: `pytest backend/tests/test_retrieval.py` passes.

### Task 13: LLM chat service
- **Action**: Implement `backend/app/ai/llm.py`: a client wrapping Ollama's chat completion endpoint (`OLLAMA_MODEL`), and `backend/app/ai/chat_service.py` with `answer_query(query, retrieved_context) -> ChatAnswer` where `ChatAnswer` has `text` and `confidence` (derive confidence heuristically for the POC — e.g. based on retrieval similarity scores from Task 12, documented clearly as a POC-level heuristic, not a claim of true model calibration). The prompt template must instruct the model to answer only from the provided context and to say "I am unable to find that information" (verbatim, per ASS2's fallback requirement) when the context doesn't support an answer. Add `backend/tests/test_chat_service.py` (mocked LLM client) verifying prompt construction includes the retrieved context and that a low-similarity retrieval yields low confidence.
- **Mirror**: Task 12's `RetrievedContext`.
- **Validate**: `pytest backend/tests/test_chat_service.py` passes.

### Task 14: Escalation & ticketing logic
- **Action**: Implement `backend/app/ai/escalation.py`: `handle_answer(chat_session_id, chat_answer, ...) -> EscalationResult` that, when `chat_answer.confidence` is below a configurable threshold (`ESCALATION_CONFIDENCE_THRESHOLD` env var, default e.g. 0.6), calls Task 8's `create_support_ticket` and `create_notification`, and returns the fallback message instead of the raw LLM text. Above threshold, returns the LLM answer as-is. Add `backend/tests/test_escalation.py` covering: high-confidence answer passes through unchanged with no ticket created, low-confidence answer creates exactly one ticket + notification and returns the fallback message.
- **Mirror**: Task 8's repository functions, Task 13's `ChatAnswer`.
- **Validate**: `pytest backend/tests/test_escalation.py` passes.

### Task 15: Chat API endpoint
- **Action**: Add a `ChatMessage` model to Task 4's schema (role: user/assistant, content, chat_session_id FK, created_at, thumbs feedback field nullable) via an Alembic migration. Implement `POST /chat` in `backend/app/api/chat.py`: accepts `{session_id (optional, creates new if absent), query, driver_id?, trip_id?, vehicle_id?}`, calls Task 12 → Task 13 → Task 14 in sequence, persists the user message and assistant response as `ChatMessage` rows, returns the answer + confidence + `escalated: bool`. Protected by `require_role("customer", "support_agent")`. Add `backend/tests/test_chat_api.py` (mocked AI layer) covering: new session created on first call, existing session reused, escalation path returns `escalated: true` and the fallback text.
- **Mirror**: Tasks 6, 8, 12-14.
- **Validate**: `pytest backend/tests/test_chat_api.py` passes.

### Task 16: Proactive reporting
- **Action**: Implement `backend/app/ai/reports.py`: `generate_start_of_day_report(customer_id)` and `generate_end_of_day_report(customer_id)`, each pulling relevant data via `TelematicsDataSource` (risk alerts/vehicle health/unresolved incidents/planned routes for start-of-day; speeding/harsh-braking/deviations/fuel/driver-performance summary for end-of-day per ASS3 §1.2) and running it through Task 13's chat service to produce a natural-language summary. Expose both as `POST /reports/start-of-day` and `POST /reports/end-of-day` endpoints (manually triggerable for the POC — no real scheduler required, but structure the functions so a cron job could call them directly). Add `backend/tests/test_reports.py` (mocked AI layer) verifying each report type pulls the right data category and the endpoint returns it.
- **Mirror**: Task 10's `TelematicsDataSource`, Task 13's chat service.
- **Validate**: `pytest backend/tests/test_reports.py` passes.

### Task 17: Dashboard shell & routing
- **Action**: In `frontend/src/`, build the app shell: `AuthProvider` (stores JWT from Task 6's `/auth/login`, attaches to `apiClient` requests), protected route wrapper, top-level layout (nav bar with links to Overview/Routes/Drivers/Alerts), and `react-router` routes for those four screens (placeholder content is fine for screens built in later tasks). Add `frontend/src/pages/Login.tsx` calling the auth endpoint. Add vitest tests for `AuthProvider` (login success/failure) and the protected-route redirect behavior.
- **Mirror**: Task 6's `/auth/login` contract.
- **Validate**: `npm run test` passes in `frontend/`.

### Task 18: Telematics overview & route visualisation screen
- **Action**: Build `frontend/src/pages/Overview.tsx`: fetches drivers/vehicles/trips via Task 7's endpoints, renders a summary panel plus a route list (a simple list/table view of trips with start/end and distance is sufficient for the POC — no map SDK dependency required; note this simplification in the report). Add a vitest test mocking the API client and asserting the trip list renders seeded-style data.
- **Mirror**: Task 7's API contracts, Task 17's shell/auth.
- **Validate**: `npm run test` passes for the new component.

### Task 19: Driver performance panels
- **Action**: Build `frontend/src/pages/Drivers.tsx`: lists drivers, and per-driver detail panel showing driving-event counts by type (speeding/harsh braking/idling/route deviation) fetched via Task 7's `GET /trips/{id}/events`, with a visual indicator (color-coded badge) per event type per ASS3 §2.2. Add a vitest test asserting event-type badges render correctly for mocked event data.
- **Mirror**: Task 7's API contracts, Task 18's data-fetch pattern.
- **Validate**: `npm run test` passes for the new component.

### Task 20: Alerts/notifications screen
- **Action**: Build `frontend/src/pages/Alerts.tsx`: lists `SupportTicket`/`Notification` records for the logged-in customer (needs a `GET /tickets` and `GET /notifications` read endpoint added to `backend/app/api/chat.py`, protected the same way as Task 7 — add this backend piece as part of this task since it's the minimal surface the screen needs), with status indicators (open/in-progress/resolved). Add a backend test for the two new list endpoints and a frontend vitest test for the screen.
- **Mirror**: Task 7's RBAC/filtering pattern for the new backend endpoints; Task 8's repository for ticket/notification data.
- **Validate**: `pytest backend/tests/test_tickets_api.py` and the new frontend test both pass.

### Task 21: Floating chat widget
- **Action**: Build `frontend/src/components/ChatWidget.tsx`: a persistent floating panel (per ASS3 §2.2 — visible across all routes, not a separate page) that reads the currently-selected driver/trip/vehicle context (lift this selection into a small context/store shared with Tasks 18-19), sends queries to Task 15's `POST /chat`, and renders the conversation with a visible "You are talking to an AI assistant" disclosure banner on first open (per ASS2's transparency requirement). Add vitest tests: widget sends the selected context with the query, displays the assistant's response, shows the escalation fallback message distinctly when `escalated: true`.
- **Mirror**: Task 15's `/chat` contract, Task 17's shell.
- **Validate**: `npm run test` passes for the widget.

### Task 22: Feedback loop UI
- **Action**: Extend `ChatWidget.tsx` with thumbs up/down on each assistant message (per ASS3 §4.3), calling a new `PATCH /chat/messages/{id}/feedback` backend endpoint (add to `backend/app/api/chat.py`, updates the `ChatMessage.feedback` field from Task 15) — thumbs-down triggers the same escalation path as Task 14 (create ticket if one doesn't already exist for the session). Add a brief post-resolution CES micro-survey component (`frontend/src/components/CesSurvey.tsx`, shown when a session ends) that posts to a new `POST /chat/sessions/{id}/survey` endpoint storing the score on `ChatSession`. Add backend tests for both new endpoints and frontend tests for the thumbs-down escalation trigger and survey submission.
- **Mirror**: Task 14's escalation logic, Task 21's widget.
- **Validate**: `pytest backend/tests/test_feedback_api.py` and the new frontend tests pass.

### Task 23: Security & compliance hardening
- **Action**: (a) Encrypt PII columns (`Customer.email`, `Customer.phone_number`, `Customer.full_name`) at rest using a symmetric encryption helper (`backend/app/security/crypto.py`, key from `ENCRYPTION_KEY` env var) applied via SQLAlchemy type decorators on the Task 4 models. (b) Add an `AuditLog` table + `backend/app/security/audit.py` logging every AI-generated recommendation (chat answers, reports) and every support-agent action (ticket status change) with actor, action, timestamp. (c) Add HTTPS-readiness notes to the README (reverse-proxy/TLS termination is out of scope for local Docker Compose, document why). Add `backend/tests/test_crypto.py` (round-trip encrypt/decrypt) and `backend/tests/test_audit.py` (chat answer and ticket status change both produce an audit entry).
- **Mirror**: Task 4's models (adds type decorators, doesn't restructure them), Task 14/16's AI outputs (source of audit events).
- **Validate**: `pytest backend/tests/test_crypto.py backend/tests/test_audit.py` passes; run `ecc:security-reviewer` against the diff and resolve any Critical/Important findings before marking this task complete.

## Future Work (not built in this plan — scaffolded via Task 10)
Databricks integration: implement `DatabricksDataSource(TelematicsDataSource)` once real data access is granted, and a RAG-corpus refresh pipeline pulling `KnowledgeBaseArticle` content from Databricks. No task above should hardcode assumptions that block this swap.

## Validation (whole-plan)
```bash
cd backend && pytest
cd frontend && npm run test && npm run build
docker compose config
```

## Acceptance
- [ ] All 23 tasks complete, each with passing tests
- [ ] Final whole-branch review clean (or findings parked with rulings)
- [ ] `docker compose config` validates the full stack definition
- [ ] Security-reviewer pass clean on Task 23's diff
