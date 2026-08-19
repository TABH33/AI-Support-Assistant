"""Main FastAPI application."""

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.telematics import router as telematics_router

app = FastAPI(title="Telematics AI Assistant")

app.include_router(auth_router)
app.include_router(telematics_router)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
