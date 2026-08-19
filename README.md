# AI-Driven Telematics Support Assistant

A FastAPI backend + React frontend application with RAG/LLM chat capabilities for fleet management.

## Prerequisites

- Docker and Docker Compose (version 3.8+)
- Git

## Quick Start

### 1. Clone and Setup

```bash
# Copy environment configuration
cp .env.example .env
```

### 2. Start the Full Stack

```bash
docker compose up
```

This command will:
- Start PostgreSQL (with pgvector) on port 5432
- Start Ollama on port 11434
- Build and start the FastAPI backend on port 8000
- Build and start the React frontend on port 3000

Once all services are healthy, open your browser and navigate to:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000/api
- **API Docs**: http://localhost:8000/docs

## Ollama Model Setup

Before using the chat features, you need to pull the required Ollama models.

### Pull Models (inside or outside Docker)

Once Ollama is running, pull the required models:

```bash
# Pull the main language model (llama2)
ollama pull llama2

# Pull the embedding model
ollama pull nomic-embed-text
```

If running Docker Compose, you can exec into the ollama container:

```bash
docker exec telematics_ollama ollama pull llama2
docker exec telematics_ollama ollama pull nomic-embed-text
```

Alternatively, pull models on your host machine before starting Docker (if Ollama is installed locally), and they will be available via the network URL.

## Environment Configuration

The `.env` file controls all service configurations:

- `POSTGRES_USER/PASSWORD/DB`: PostgreSQL credentials and database name
- `DATABASE_URL`: Connection string for the backend to reach PostgreSQL
- `OLLAMA_BASE_URL`: URL for the backend to communicate with Ollama
- `OLLAMA_MODEL`: Main language model name (e.g., llama2)
- `OLLAMA_EMBED_MODEL`: Embedding model name (e.g., nomic-embed-text)
- `JWT_SECRET`: Secret key for JWT token signing (change in production)
- `SESSION_TIMEOUT_MINUTES`: Session timeout duration
- `VITE_API_BASE_URL`: Frontend API endpoint (baked at build time; changing this requires `docker compose build frontend` before restarting)

## Running Tests Locally

Tests are run locally (outside Docker) against the source trees. The production Docker images do not include test dependencies or development files.

### Backend Tests

```bash
cd backend
pip install -e ".[dev]"
pytest
```

### Frontend Tests

```bash
cd frontend
npm install
npm run test
```

## Development Workflow

### Rebuilding Services

If you modify the Dockerfiles or dependencies:

```bash
# Rebuild without cache
docker compose build --no-cache

# Then restart
docker compose up
```

### Viewing Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f postgres
docker compose logs -f ollama
```

### Stopping Services

```bash
# Stop all services (keeps volumes)
docker compose down

# Stop and remove volumes (reset database)
docker compose down -v
```

## Architecture

- **Backend**: FastAPI application with RAG/LLM integration via Ollama
- **Frontend**: React 18 + Vite application
- **Database**: PostgreSQL with pgvector extension for vector embeddings
- **LLM**: Ollama for local language model and embedding inference

## Troubleshooting

### Services fail to start
- Check Docker and Docker Compose are installed: `docker --version && docker compose --version`
- Ensure ports 5432, 11434, 8000, 3000 are available
- Check logs: `docker compose logs`

### Backend can't connect to database
- Verify `DATABASE_URL` in `.env` matches PostgreSQL service name and credentials
- Ensure postgres service is healthy: `docker compose ps`

### Frontend can't reach backend API
- Verify `VITE_API_BASE_URL` is set to the correct backend URL
- Rebuild frontend after changing: `docker compose build frontend`

### Ollama models not available
- Pull models as described in "Ollama Model Setup" section
- Check Ollama is running: `docker compose ps ollama`
- Verify network connectivity: `docker compose exec backend curl http://ollama:11434/api/tags`
