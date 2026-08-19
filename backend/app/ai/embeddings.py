"""Thin client for Ollama's embeddings endpoint (Task 11).

Per the plan's Global Constraints, this is the single module that talks to
Ollama for embeddings -- everything downstream (the indexing script here,
and RAG retrieval in later tasks) goes through `embed_text`, never `httpx`
directly.

API shape (Ollama's actual `/api/embeddings` contract):
    POST {OLLAMA_BASE_URL}/api/embeddings
    body: {"model": "<model>", "prompt": "<text>"}
    response: {"embedding": [<768 floats for nomic-embed-text>, ...]}
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_EMBEDDINGS_PATH = "/api/embeddings"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class EmbeddingError(RuntimeError):
    """Base exception for `embed_text` failures."""


class EmbeddingRequestError(EmbeddingError):
    """The HTTP call to Ollama's embeddings endpoint failed outright --
    either a network/connection error, or a non-2xx HTTP status."""


class EmbeddingResponseError(EmbeddingError):
    """Ollama returned a 2xx response that isn't a usable embedding: not
    valid JSON, or missing/malformed the "embedding" field."""


def embed_text(text: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> list[float]:
    """Embed `text` via Ollama's `/api/embeddings` endpoint, using the
    configured `settings.ollama_base_url` / `settings.ollama_embed_model`
    (`nomic-embed-text`, 768 dimensions, by default).

    Raises `EmbeddingRequestError` if the HTTP call itself fails (network
    error or non-2xx status), and `EmbeddingResponseError` if Ollama's
    response body can't be parsed as an embedding. Both cases are logged
    before the exception is raised -- this is infrastructure code, so a
    failure must be visible/loggable, never silently swallowed into a
    fabricated fallback vector.
    """
    url = f"{settings.ollama_base_url.rstrip('/')}{_EMBEDDINGS_PATH}"
    payload = {"model": settings.ollama_embed_model, "prompt": text}

    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Ollama embeddings request to %s failed: %s", url, exc)
        raise EmbeddingRequestError(f"Ollama embeddings request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        logger.error("Ollama embeddings response from %s was not valid JSON: %s", url, exc)
        raise EmbeddingResponseError(
            "Ollama embeddings response was not valid JSON"
        ) from exc

    embedding = data.get("embedding") if isinstance(data, dict) else None
    if not isinstance(embedding, list) or not embedding:
        logger.error(
            "Ollama embeddings response from %s missing a usable 'embedding' field: %r",
            url,
            data,
        )
        raise EmbeddingResponseError(
            "Ollama embeddings response missing a usable 'embedding' field"
        )

    try:
        return [float(x) for x in embedding]
    except (TypeError, ValueError) as exc:
        logger.error("Ollama embeddings response 'embedding' field wasn't all numbers: %r", embedding)
        raise EmbeddingResponseError(
            "Ollama embeddings response 'embedding' field wasn't all numbers"
        ) from exc
