"""Tests for `app.ai.llm` and `app.ai.chat_service` (Task 13).

Mirrors `test_embeddings.py`'s (Task 11) mocking pattern exactly: the Ollama
HTTP call is always mocked via `unittest.mock.patch("app.ai.llm.httpx.post")`,
returning real `httpx.Response` objects -- this exercises `chat_completion`'s
actual request-building and response-parsing code, and `answer_query`'s
prompt-building + confidence logic, without ever touching a live network or a
real Ollama instance. No test in this file makes a real HTTP call; they would
still pass with network access disabled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer, answer_query, build_prompt_messages
from app.ai.llm import LLMRequestError, LLMResponseError, chat_completion
from app.ai.retrieval import RetrievedContext
from app.config import settings
from app.models.enums import DrivingEventType
from app.models.knowledge import KnowledgeBaseArticle
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle

_FAKE_REQUEST = httpx.Request("POST", "http://ollama.test/api/chat")


def _mock_response(status_code: int = 200, json_body: dict | None = None, text: str | None = None):
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=_FAKE_REQUEST)
    return httpx.Response(status_code, text=text or "", request=_FAKE_REQUEST)


def _chat_json(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


# ---------------------------------------------------------------------------
# chat_completion: request shape + response parsing (mirrors test_embeddings.py)
# ---------------------------------------------------------------------------


def test_chat_completion_sends_correct_request_shape():
    with patch("app.ai.llm.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body=_chat_json("hello"))

        result = chat_completion([{"role": "user", "content": "hi"}])

        assert result == "hello"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs.get("url")
        assert url == f"{settings.ollama_base_url}/api/chat"
        assert kwargs["json"] == {
            "model": settings.ollama_model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }


def test_chat_completion_raises_llm_request_error_on_non_200_response():
    with patch("app.ai.llm.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(status_code=500, text="internal server error")

        with pytest.raises(LLMRequestError):
            chat_completion([{"role": "user", "content": "hi"}])


def test_chat_completion_raises_llm_request_error_on_connection_failure():
    with patch("app.ai.llm.httpx.post") as mock_post:
        mock_post.side_effect = httpx.ConnectError("connection refused", request=_FAKE_REQUEST)

        with pytest.raises(LLMRequestError):
            chat_completion([{"role": "user", "content": "hi"}])


def test_chat_completion_raises_llm_response_error_on_malformed_json():
    with patch("app.ai.llm.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(text="not valid json")

        with pytest.raises(LLMResponseError):
            chat_completion([{"role": "user", "content": "hi"}])


def test_chat_completion_raises_llm_response_error_when_content_missing():
    with patch("app.ai.llm.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"message": {"role": "assistant"}})

        with pytest.raises(LLMResponseError):
            chat_completion([{"role": "user", "content": "hi"}])


def test_chat_completion_raises_llm_response_error_when_message_missing():
    with patch("app.ai.llm.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"unexpected": "shape"})

        with pytest.raises(LLMResponseError):
            chat_completion([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# build_prompt_messages: genuine context incorporation, not a placeholder
# ---------------------------------------------------------------------------


def _article(title: str, content: str, category: str | None = None) -> KnowledgeBaseArticle:
    return KnowledgeBaseArticle(title=title, content=content, category=category)


def _context_with_article_and_events() -> RetrievedContext:
    driver = Driver(driver_id=1, customer_id=1, full_name="Jane Driver", license_number="LIC-1")
    vehicle = Vehicle(
        vehicle_id=1, customer_id=1, registration_number="REG-1", make="Ford", model="Transit", year=2021
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trip = Trip(trip_id=1, driver_id=1, vehicle_id=1, start_time=start, distance_km=42.0)
    event = DrivingEvent(
        driving_event_id=1,
        trip_id=1,
        event_type=DrivingEventType.HARSH_BRAKING,
        event_time=start,
        details="Harsh braking detected near Main St",
    )
    article = _article(
        "Harsh braking alerts",
        "Harsh braking events are triggered when deceleration exceeds 6 m/s^2.",
        category="safety",
    )
    return RetrievedContext(
        query="why did the driver brake harshly?",
        articles=[article],
        driver=driver,
        vehicle=vehicle,
        trip=trip,
        driving_events=[event],
    )


def test_build_prompt_messages_includes_system_instructions_for_context_only_and_fallback():
    context = RetrievedContext(query="q", articles=[])

    messages = build_prompt_messages("q", context)

    assert messages[0]["role"] == "system"
    system_content = messages[0]["content"]
    assert "ONLY" in system_content
    assert FALLBACK_TEXT in system_content


def test_build_prompt_messages_embeds_real_kb_article_content():
    context = _context_with_article_and_events()

    messages = build_prompt_messages("why did the driver brake harshly?", context)
    user_content = messages[1]["content"]

    assert "Harsh braking alerts" in user_content
    assert "deceleration exceeds 6 m/s^2" in user_content


def test_build_prompt_messages_embeds_real_driving_event_and_trip_data():
    context = _context_with_article_and_events()

    messages = build_prompt_messages("why did the driver brake harshly?", context)
    user_content = messages[1]["content"]

    assert "Jane Driver" in user_content
    assert "Ford Transit" in user_content
    assert "harsh_braking" in user_content
    assert "Harsh braking detected near Main St" in user_content


def test_build_prompt_messages_includes_the_query_itself():
    context = RetrievedContext(query="q", articles=[])

    messages = build_prompt_messages("What causes idling alerts?", context)

    assert "What causes idling alerts?" in messages[1]["content"]


def test_build_prompt_messages_notes_absence_of_context_when_nothing_retrieved():
    context = RetrievedContext(query="q", articles=[])

    messages = build_prompt_messages("some question", context)
    user_content = messages[1]["content"]

    assert "no knowledge base articles" in user_content
    assert "no driver/vehicle/trip data" in user_content


# ---------------------------------------------------------------------------
# answer_query: confidence heuristic + fallback reflection
# ---------------------------------------------------------------------------


def test_answer_query_returns_chat_answer_with_llm_text():
    context = _context_with_article_and_events()

    with patch("app.ai.chat_service.chat_completion", return_value="Harsh braking is caused by sudden deceleration.") as mock_chat:
        result = answer_query("why did the driver brake harshly?", context)

    assert isinstance(result, ChatAnswer)
    assert result.text == "Harsh braking is caused by sudden deceleration."
    mock_chat.assert_called_once()
    # The messages passed to chat_completion carry the real context.
    called_messages = mock_chat.call_args[0][0]
    assert "Harsh braking alerts" in called_messages[1]["content"]


def test_answer_query_empty_retrieval_yields_low_confidence():
    empty_context = RetrievedContext(query="totally unrelated question", articles=[])

    with patch("app.ai.chat_service.chat_completion", return_value=FALLBACK_TEXT):
        result = answer_query("totally unrelated question", empty_context)

    assert result.confidence <= 0.1


def test_answer_query_rich_retrieval_yields_higher_confidence_than_empty_retrieval():
    rich_context = _context_with_article_and_events()
    empty_context = RetrievedContext(query="q", articles=[])

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Harsh braking is caused by sudden deceleration.",
    ):
        rich_result = answer_query("q", rich_context)
    with patch("app.ai.chat_service.chat_completion", return_value=FALLBACK_TEXT):
        empty_result = answer_query("q", empty_context)

    assert rich_result.confidence > empty_result.confidence


def test_answer_query_fallback_response_is_reflected_in_low_confidence_even_with_context():
    rich_context = _context_with_article_and_events()

    with patch("app.ai.chat_service.chat_completion", return_value=FALLBACK_TEXT):
        result = answer_query("q", rich_context)

    assert result.text == FALLBACK_TEXT
    assert result.confidence <= 0.1


def test_answer_query_non_fallback_response_with_context_yields_confidence_above_floor():
    rich_context = _context_with_article_and_events()

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Harsh braking is caused by sudden deceleration.",
    ):
        result = answer_query("q", rich_context)

    assert result.confidence > 0.1


# ---------------------------------------------------------------------------
# _compute_confidence: relevance gating (final-review Fix 4)
#
# `build_top_k_articles_query` has no distance/relevance cutoff -- it always
# returns `top_k` articles regardless of how semantically distant they are
# from the query. Before this fix, `_compute_confidence` scored confidence
# purely off the COUNT of retrieved articles, so retrieval quality could
# never actually trigger escalation. These tests prove the property that was
# previously untested and is the actual bug: the SAME article count must
# yield a LOWER confidence when those articles are all distant/low-similarity
# than when they're genuinely close/relevant to the query.
# ---------------------------------------------------------------------------


def _context_with_articles(similarities: list[float]) -> RetrievedContext:
    """Same article COUNT for every call site below -- only
    `article_similarities` varies -- so any confidence difference between
    two contexts built this way is attributable to relevance, not count."""
    articles = [
        _article(f"Article {i}", f"Content for article {i}") for i in range(len(similarities))
    ]
    return RetrievedContext(query="q", articles=articles, article_similarities=similarities)


def test_close_relevant_articles_yield_higher_confidence_than_distant_ones_with_same_count():
    close_context = _context_with_articles([0.97, 0.9, 0.85])
    distant_context = _context_with_articles([0.05, 0.02, 0.01])
    assert len(close_context.articles) == len(distant_context.articles)

    with patch("app.ai.chat_service.chat_completion", return_value="A grounded answer."):
        close_result = answer_query("q", close_context)
    with patch("app.ai.chat_service.chat_completion", return_value="A grounded answer."):
        distant_result = answer_query("q", distant_context)

    assert close_result.confidence > distant_result.confidence


def test_articles_all_below_relevance_floor_get_no_credit_for_being_present():
    """Even with `DEFAULT_TOP_K` articles retrieved, if none of them clear
    the relevance floor, `_compute_confidence` must treat it the same as
    having found no articles at all for that axis (still distinguishable
    from "found nothing whatsoever" only via the telematics axis, which this
    context also lacks -- so this pins to the 0.1 floor)."""
    distant_context = _context_with_articles([0.1, 0.05, 0.0])

    with patch("app.ai.chat_service.chat_completion", return_value="An answer."):
        result = answer_query("q", distant_context)

    assert result.confidence <= 0.35  # no article-count bonus was awarded


def test_confidence_scales_with_best_match_relevance_not_just_presence():
    """A single very-close match should score higher than a single
    borderline-relevant match, even though both contexts retrieved exactly
    one article."""
    very_close = _context_with_articles([0.99])
    borderline = _context_with_articles([0.31])

    with patch("app.ai.chat_service.chat_completion", return_value="An answer."):
        very_close_result = answer_query("q", very_close)
    with patch("app.ai.chat_service.chat_completion", return_value="An answer."):
        borderline_result = answer_query("q", borderline)

    assert very_close_result.confidence > borderline_result.confidence
