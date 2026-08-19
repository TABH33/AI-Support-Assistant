"""Thin client for Ollama's chat completion endpoint (Task 13).

Mirrors Task 11's `app.ai.embeddings` module: this is the single module that
talks to Ollama for chat completions -- everything downstream (Task 13's
`chat_service.answer_query`, and any later task that needs an LLM response)
goes through `chat_completion`, never `httpx` directly. Per the plan's Global
Constraints, all LLM calls go through one service module.

API shape (Ollama's actual `/api/chat` contract -- the modern, messages-array
endpoint, preferred here over the older single-string `/api/generate`):
    POST {OLLAMA_BASE_URL}/api/chat
    body: {
        "model": "<model>",
        "messages": [{"role": "system"|"user"|"assistant", "content": "<text>"}, ...],
        "stream": false,
    }
    response: {"message": {"role": "assistant", "content": "<text>"}, ...}

`stream: false` is passed explicitly so the response is a single JSON object
(not newline-delimited streaming chunks) -- matching how `embed_text` expects
one JSON body back from `/api/embeddings`.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_CHAT_PATH = "/api/chat"
_DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMError(RuntimeError):
    """Base exception for `chat_completion` failures."""


class LLMRequestError(LLMError):
    """The HTTP call to Ollama's chat endpoint failed outright -- either a
    network/connection error, or a non-2xx HTTP status."""


class LLMResponseError(LLMError):
    """Ollama returned a 2xx response that isn't a usable chat completion:
    not valid JSON, or missing/malformed the "message.content" field."""


def chat_completion(
    messages: list[dict[str, str]], *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> str:
    """Send `messages` (a list of `{"role": ..., "content": ...}` dicts) to
    Ollama's `/api/chat` endpoint, using the configured
    `settings.ollama_base_url` / `settings.ollama_model`, and return the
    assistant's reply text.

    Raises `LLMRequestError` if the HTTP call itself fails (network error or
    non-2xx status), and `LLMResponseError` if Ollama's response body can't
    be parsed as a chat completion. Both cases are logged before the
    exception is raised -- this is infrastructure code, so a failure must be
    visible/loggable, never silently swallowed into a fabricated response.
    """
    url = f"{settings.ollama_base_url.rstrip('/')}{_CHAT_PATH}"
    payload = {"model": settings.ollama_model, "messages": messages, "stream": False}

    try:
        response = httpx.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Ollama chat request to %s failed: %s", url, exc)
        raise LLMRequestError(f"Ollama chat request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        logger.error("Ollama chat response from %s was not valid JSON: %s", url, exc)
        raise LLMResponseError("Ollama chat response was not valid JSON") from exc

    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content:
        logger.error(
            "Ollama chat response from %s missing a usable 'message.content' field: %r",
            url,
            data,
        )
        raise LLMResponseError(
            "Ollama chat response missing a usable 'message.content' field"
        )

    return content
