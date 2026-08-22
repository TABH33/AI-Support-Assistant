# AI-Driven Telematics Support Assistant — Documentation

This directory is the technical reference for the project. It documents the
system as it actually exists in this repository, not as originally proposed —
where the two differ (and they do, in a few places), the docs note the
divergence and why.

Built for **Ctrack Australia Pty Ltd** as an ICT307 (CIHE) capstone project,
based on the three source reports in the repo root:
`ICT307_AI-Driven_Telematics_Support_Assistant_report.docx` (Proposal),
`ICT307_ASS2_..._report.docx` (Requirement Analysis), and
`ICT307_ASS3_..._report.docx` (System Architecture).

## Contents

| Document | Covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System overview, components, request flow, why each major technical choice was made |
| [DATA_MODEL.md](DATA_MODEL.md) | All 13 database entities, their fields, relationships, and the ER diagram |
| [API_REFERENCE.md](API_REFERENCE.md) | Every HTTP endpoint: method, path, auth, request/response shape |
| [RAG_PIPELINE.md](RAG_PIPELINE.md) | How chat questions are answered — retrieval, confidence scoring, escalation, reports |
| [SECURITY.md](SECURITY.md) | Auth, multi-tenant isolation, PII encryption, audit logging |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Running the stack with Docker Compose, environment variables, troubleshooting |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local dev workflow, running tests, project layout, known limitations |

## Quick orientation

- **Backend**: FastAPI + SQLAlchemy 2.x + Alembic, Python, in `backend/`.
- **Frontend**: React 18 + TypeScript + Vite + Tailwind, in `frontend/`.
- **Database**: PostgreSQL 15+ with the `pgvector` extension.
- **LLM**: Ollama running locally (no external API calls, no per-token cost)
  — `llama3.1:8b` for chat/report generation, `nomic-embed-text` for
  embeddings.
- **Core feature**: a RAG (Retrieval-Augmented Generation) chat assistant
  that answers customer questions from a knowledge base plus live telematics
  data, escalating to a human support ticket whenever it isn't confident in
  its own answer.

If you only read one other document, read [ARCHITECTURE.md](ARCHITECTURE.md)
first — everything else assumes it.
