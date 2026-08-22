# Development

## Project layout

```
backend/
  app/
    api/            HTTP route handlers (auth, telematics, chat, reports)
    ai/              RAG pipeline: embeddings, retrieval, chat_service, escalation, reports, llm
    auth/            Password hashing, JWT, RBAC dependency
    datasources/     TelematicsDataSource interface + SyntheticDataSource
    models/          SQLAlchemy ORM entities
    repositories/    Chat session/message/ticket persistence helpers
    security/        At-rest crypto, audit logging
    jobs/            retention.py — expired chat-session purge
    seed/            Synthetic fleet-data generator + runnable seed script
  alembic/           Migrations
  tests/             pytest suite
  Dockerfile
  pyproject.toml
frontend/
  src/
    pages/           Overview, Routes, Drivers, Alerts (dashboard screens)
    components/      ChatWidget, Layout, ProtectedRoute, CesSurvey
    context/         AuthProvider (JWT/session state), SelectionContext
    lib/apiClient.ts  Fetch wrapper attaching the bearer token
    types/           Shared TS types for chat/telematics API shapes
  Dockerfile
  package.json
docs/                This directory
docker-compose.yml
.env.example         Root-level (Postgres/Ollama/Docker Compose) secrets
backend/.env.example  Backend-only secrets (for running the backend outside Docker)
```

## Running the backend locally (outside Docker)

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env   # point DATABASE_URL/OLLAMA_BASE_URL at wherever those services run
alembic upgrade head
uvicorn app.main:app --reload
```

Requires a reachable Postgres with `pgvector` installed and a reachable
Ollama instance with both models pulled — running `docker compose up
postgres ollama` and pointing `DATABASE_URL`/`OLLAMA_BASE_URL` at their
exposed ports is the easiest way to get both without also running the
backend/frontend in Docker.

## Running the frontend locally

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173
```

`VITE_API_BASE_URL` for local dev defaults to pointing at
`http://localhost:8000` (see `README.md`); it's read at **build** time by
Vite, so changing it requires restarting `npm run dev` (or, for the Docker
image, a rebuild — it's baked into the built JS bundle, not read at
container-runtime).

## Tests

**Backend** (pytest, no live Postgres/Ollama required — tests use SQLite
and mock the Ollama HTTP calls; the one Postgres-only pgvector query is
verified by SQL-dialect compilation, not execution):

```bash
cd backend
pip install -e ".[dev]"
pytest
```

**Frontend** (Vitest + Testing Library + jsdom):

```bash
cd frontend
npm run test          # headless
npm run test:ui       # interactive UI
```

Both suites run against source trees directly — the production Docker
images deliberately exclude test dependencies and dev files (`tests/` is
not `COPY`'d into the backend image; see `backend/Dockerfile`).

## Adding a knowledge-base article

Articles live in `knowledge_base_articles` with a nullable `embedding`
column. A newly-inserted article won't be retrievable until it's embedded:

```bash
docker compose exec backend python -c "
from app.database import SessionLocal
from app.ai.index_kb import index_knowledge_base
db = SessionLocal()
print(index_knowledge_base(db), 'articles indexed')
"
```

`index_knowledge_base` (`app/ai/index_kb.py`) embeds `article.content` only
— title and category are not embedded — and commits once at the end.

## Code organization principles worth knowing before changing things

- **`TelematicsDataSource` is the only sanctioned path to telematics data**
  from `ai/` or `api/telematics.py` — never a raw `db.query(Driver)` etc.
  from those layers. This is what keeps a future Databricks-backed
  implementation a drop-in swap. See [ARCHITECTURE.md](ARCHITECTURE.md).
- **`customer_id` is always derived server-side**, never trusted from a
  request body/query string for actual data scoping — see
  [SECURITY.md](SECURITY.md) before touching any endpoint that touches
  customer-owned data.
- **`FALLBACK_TEXT` in `app/ai/chat_service.py` is load-bearing** — it's
  pattern-matched verbatim by the escalation and confidence logic. Never
  reword it without updating every call site that checks for it.
- **One commit per request** in the chat/report/feedback/survey endpoints —
  session/message/ticket/notification/audit-log writes within a single
  HTTP request all share one `db.commit()` at the very end, not one each.
  Preserve this pattern in any new endpoint that writes more than one row.

## Known limitations / open items

- **No `DatabricksDataSource`.** Connecting the RAG pipeline to Ctrack's
  real Databricks telematics data (instead of the synthetic seeded data) is
  a planned follow-on integration, not yet built — it requires manual VPN
  login to Ctrack's Azure Databricks workspace, and the schema/auth
  approach were deliberately not guessed at. See
  [ARCHITECTURE.md](ARCHITECTURE.md#why-telematicsdatasource-is-a-protocol-not-a-concrete-class).
- **No fleet-wide aggregate/count queries** (e.g. "how many Toyota Hilux do
  we have?") — the RAG pipeline only ever resolves a single
  driver/vehicle/trip by id, with no aggregation step. See
  [RAG_PIPELINE.md](RAG_PIPELINE.md#known-scope-limits).
- **No signup endpoint.** Accounts exist only via the seed script or direct
  DB writes — see [DEPLOYMENT.md](DEPLOYMENT.md#seeding-demo-data).
- **No scheduler for reports.** `generate_start_of_day_report`/
  `generate_end_of_day_report` are plain callable functions a future cron
  job could invoke directly; nothing in this codebase schedules them — they
  are reached only via `POST /reports/*` or the chat report-intent router.
- **A narrow concurrent-request race exists on the thumbs-down escalation
  path** (two simultaneous thumbs-down submissions on the same message):
  explicitly identified during final review and consciously left unfixed as
  non-load-bearing for this POC's realistic traffic patterns, rather than
  adding complexity for a scenario very unlikely to occur in practice.
