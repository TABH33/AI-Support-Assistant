# Deployment

## Stack

`docker-compose.yml` at the repo root defines four services:

| Service | Image / build | Port | Notes |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 5432 | Health-gated (`pg_isready`) |
| `ollama` | `ollama/ollama` | 11434 | Health-gated (TCP check on 11434); models are **not** pulled automatically — see below |
| `backend` | `./backend/Dockerfile` | 8000 | Depends on both above being healthy; health-gated on `GET /health` |
| `frontend` | `./frontend/Dockerfile` | 3000 | Depends on `backend` being healthy; `VITE_API_BASE_URL` is baked in at **build** time, not read at runtime |

## Standard local deployment

```bash
cp .env.example .env      # then edit secrets — see "Required secrets" below
docker compose up -d --build
```

Pull the two Ollama models (not bundled in the image, must be pulled once
after the `ollama` container is up):

```bash
docker exec telematics_ollama ollama pull llama3.1:8b
docker exec telematics_ollama ollama pull nomic-embed-text
```

Then open `http://localhost:3000` (dashboard + chat widget) or
`http://localhost:8000/docs` (API docs).

## Required secrets (`.env`)

See `.env.example` (root) and `backend/.env.example` for the full annotated
list. The ones that matter operationally:

| Variable | Purpose | Generate with |
|---|---|---|
| `POSTGRES_PASSWORD` | Postgres auth | any strong random string |
| `JWT_SECRET` | Signs auth tokens | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ENCRYPTION_KEY` | At-rest PII encryption (see [SECURITY.md](SECURITY.md)) | `python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"` |
| `OLLAMA_MODEL` | Chat/report LLM | must be a real, pulled Ollama tag — see pitfall below |

**Never reuse the placeholder values committed in `.env.example`/
`backend/.env.example` for a real deployment** — they exist only so the
documented quickstart works out of the box.

## Database migrations

Alembic-managed (`backend/alembic/`). The backend image's entrypoint runs
`alembic upgrade head` on startup — no manual migration step is required for
a fresh `docker compose up`. To run migrations manually (e.g. against a
pre-existing DB):

```bash
docker compose exec backend alembic upgrade head
```

## Seeding demo data

```bash
docker compose exec backend python -m app.seed.seed
```

Generates a synthetic fleet (customers, devices, drivers, vehicles, trips,
driving events, a knowledge-base article set) via
`backend/app/seed/generator.py`. **Seeded `Customer`/`SupportAgent` rows get
a placeholder `password_hash` that cannot log in** (`"seed$unhashed$..."`)
— there is no signup endpoint, so to actually log in as a seeded account you
must set a real password hash directly, e.g. from inside the backend
container:

```python
from app.database import SessionLocal
from app.auth.security import hash_password
from app.models.customer import Customer

db = SessionLocal()
customer = db.get(Customer, 1)
customer.password_hash = hash_password("YourChosenPassword!")
db.commit()
```

(Same pattern for `SupportAgent`, using its `support_agent_id` PK.)

## Known pitfalls (found deploying this exact repo)

These are real bugs hit and fixed while standing this project up for the
first time — none were caught by the test suite, since no automated test
exercises a real Docker build + live Postgres + live Ollama cycle.

1. **`alembic upgrade head` fails inside the container**
   (`FAILED: No 'script_location' key found in configuration`) — the
   backend `Dockerfile` must `COPY alembic.ini .` and `COPY alembic/
   ./alembic/`, not just `app/` and `pyproject.toml`. (Already fixed in this
   repo's `backend/Dockerfile`.)
2. **`error: Multiple top-level packages discovered in a flat-layout`** —
   once `alembic/` is copied alongside `app/`, `setuptools`' auto-discovery
   sees two top-level packages. Fixed via
   `[tool.setuptools.packages.find] include = ["app*"]` in
   `backend/pyproject.toml`. (Already fixed in this repo.)
3. **Invalid Ollama model tag silently "succeeds" then 500s at chat time** —
   pulling a tag that doesn't exist (e.g. `llama3.1:8b-instruct`, which is
   not a real Ollama library tag) reports `Error: pull model manifest: file
   does not exist` on the pull itself but doesn't fail the surrounding
   script; the first real chat request then 500s with
   `LLMRequestError: ... 404 Not Found ... /api/chat`. Always verify a
   model actually pulled with `docker exec telematics_ollama ollama list`
   before assuming `OLLAMA_MODEL`/`OLLAMA_EMBED_MODEL` are correct. This
   repo's default (`llama3.1:8b`) is a verified-working tag.
4. **A `docker cp` hot-patch into a running container is not a real
   deploy** — it only affects the running container's filesystem, not the
   built image, and is lost on the next `docker compose up`/restart. After
   any code change intended to be permanent, rebuild the image
   (`docker compose build backend && docker compose up -d backend`) and
   confirm the change landed (e.g. `docker exec telematics_backend grep -n
   "<marker>" /app/app/...`) rather than trusting the hot-patch alone.

## Deploying inside WSL2 from a Windows-side git checkout

If the repository lives in a private GitHub remote and you don't want to
configure git credentials inside the WSL distro, `git clone` from inside
WSL will hang waiting for auth. Instead, export the working tree from the
Windows-side checkout (which already has credentials) directly into the WSL
filesystem:

```bash
# from inside WSL, with the Windows repo mounted at /mnt/c/...
git config --global --add safe.directory /mnt/c/Users/<you>/path/to/repo
cd /mnt/c/Users/<you>/path/to/repo
git archive HEAD | tar -x -C ~/your-deploy-dir
```

This is a purely local git operation — no network, no auth — and copies
exactly the committed tree (respecting `.gitignore`), so a hand-crafted
`.env` in `~/your-deploy-dir` survives repeated re-syncs (archive only
overwrites tracked files). Re-run the same `git archive | tar -x` after every
commit you want reflected in the deployment, then rebuild
(`docker compose build <service> && docker compose up -d <service>`) — the
archive step alone does **not** update a running container.

Docker itself requires the invoking user to be in the `docker` group inside
the WSL distro (`sudo usermod -aG docker <your-wsl-username>` — take care to
use your actual username, not `$USER` from a root shell, which resolves to
`root`). WSL2 automatically forwards `localhost` ports from the distro to
Windows, so `http://localhost:3000`/`:8000` work from a Windows browser with
no extra port-forwarding configuration.

## Troubleshooting

See `README.md`'s own "Troubleshooting" section for the standard checklist
(ports in use, `DATABASE_URL` mismatches, `VITE_API_BASE_URL` rebuild
requirement, etc.) — it's accurate and not duplicated here.
