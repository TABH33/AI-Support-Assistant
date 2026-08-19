"""Main FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.reports import router as reports_router
from app.api.telematics import router as telematics_router
from app.config import settings

app = FastAPI(title="Telematics AI Assistant")

# Without this, the browser blocks every response before any frontend JS
# ever sees it -- the frontend (Vite dev server on :5173, or the Docker
# Compose `frontend` service on :3000) is always a different origin from
# this API (:8000), so every request is cross-origin. `allow_credentials`
# is False because the frontend never sends cookies -- auth is a bearer
# token attached manually via the `Authorization` header (see
# `frontend/src/lib/apiClient.ts`), which CORS treats as a "simple"
# credential-less header from the browser's perspective.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router)
app.include_router(telematics_router)
app.include_router(chat_router)
app.include_router(reports_router)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
