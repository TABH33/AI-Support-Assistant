"""Main FastAPI application."""

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.reports import router as reports_router
from app.api.telematics import router as telematics_router

app = FastAPI(title="Telematics AI Assistant")

app.include_router(auth_router)
app.include_router(telematics_router)
app.include_router(chat_router)
app.include_router(reports_router)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
