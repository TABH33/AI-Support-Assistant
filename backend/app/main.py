"""Main FastAPI application."""

from fastapi import FastAPI

app = FastAPI(title="Telematics AI Assistant")


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
