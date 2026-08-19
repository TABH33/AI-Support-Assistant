"""Tests for `app.ai.embeddings` and `app.ai.index_kb` (Task 11).

The Ollama HTTP call is always mocked via
`unittest.mock.patch("app.ai.embeddings.httpx.post")`, returning real
`httpx.Response` objects -- this exercises `embed_text`'s actual
request-building and response-parsing code without ever touching a live
network or a real Ollama instance. No test in this file makes a real HTTP
call; they would still pass with network access disabled.

`index_knowledge_base` tests use the same in-memory SQLite fixture pattern as
`test_datasource.py` (Task 10): a real DB round-trip through
`SyntheticDataSource`, with only the Ollama call mocked -- so these tests
prove the `embedding` column is actually populated after the script's core
logic runs, not just that mocks were called.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.embeddings import EmbeddingRequestError, EmbeddingResponseError, embed_text
from app.ai.index_kb import index_knowledge_base
from app.config import settings
from app.datasources.synthetic import SyntheticDataSource
from app.models import Base, KnowledgeBaseArticle

FAKE_EMBEDDING = [round(i / 1000, 3) for i in range(768)]
_FAKE_REQUEST = httpx.Request("POST", "http://ollama.test/api/embeddings")


def _mock_response(status_code: int = 200, json_body: dict | None = None, text: str | None = None):
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=_FAKE_REQUEST)
    return httpx.Response(status_code, text=text or "", request=_FAKE_REQUEST)


# ---------------------------------------------------------------------------
# embed_text: request shape + response parsing
# ---------------------------------------------------------------------------


def test_embed_text_sends_correct_request_shape():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"embedding": FAKE_EMBEDDING})

        result = embed_text("How do I reset my GPS tracker?")

        assert result == FAKE_EMBEDDING
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs.get("url")
        assert url == f"{settings.ollama_base_url}/api/embeddings"
        assert kwargs["json"] == {
            "model": settings.ollama_embed_model,
            "prompt": "How do I reset my GPS tracker?",
        }


def test_embed_text_returns_768_dim_vector_of_floats():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"embedding": FAKE_EMBEDDING})

        result = embed_text("some knowledge base content")

        assert len(result) == 768
        assert all(isinstance(x, float) for x in result)


def test_embed_text_coerces_int_embedding_values_to_float():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"embedding": [1, 2, 3]})

        result = embed_text("text")

        assert result == [1.0, 2.0, 3.0]
        assert all(isinstance(x, float) for x in result)


def test_embed_text_raises_embedding_request_error_on_non_200_response():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(status_code=500, text="internal server error")

        with pytest.raises(EmbeddingRequestError):
            embed_text("text")


def test_embed_text_raises_embedding_request_error_on_connection_failure():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.side_effect = httpx.ConnectError("connection refused", request=_FAKE_REQUEST)

        with pytest.raises(EmbeddingRequestError):
            embed_text("text")


def test_embed_text_raises_embedding_response_error_on_malformed_json():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(text="not valid json")

        with pytest.raises(EmbeddingResponseError):
            embed_text("text")


def test_embed_text_raises_embedding_response_error_when_field_missing():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"unexpected": "shape"})

        with pytest.raises(EmbeddingResponseError):
            embed_text("text")


def test_embed_text_raises_embedding_response_error_on_empty_embedding():
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"embedding": []})

        with pytest.raises(EmbeddingResponseError):
            embed_text("text")


# ---------------------------------------------------------------------------
# index_knowledge_base: real DB round-trip, mocked Ollama only
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as db_session:
        yield db_session


def test_index_knowledge_base_embeds_and_persists_all_articles(session):
    article_a = KnowledgeBaseArticle(
        title="GPS troubleshooting", content="Reset your GPS tracker by holding the button..."
    )
    article_b = KnowledgeBaseArticle(
        title="Billing FAQ", content="Invoices are generated on the 1st of each month..."
    )
    session.add_all([article_a, article_b])
    session.commit()
    article_a_id, article_b_id = article_a.knowledge_base_article_id, article_b.knowledge_base_article_id

    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"embedding": FAKE_EMBEDDING})

        count = index_knowledge_base(session, SyntheticDataSource(session))

    assert count == 2
    assert mock_post.call_count == 2

    session.expire_all()
    refreshed = (
        session.query(KnowledgeBaseArticle)
        .filter(KnowledgeBaseArticle.knowledge_base_article_id.in_([article_a_id, article_b_id]))
        .all()
    )
    assert len(refreshed) == 2
    for article in refreshed:
        assert article.embedding is not None
        assert len(article.embedding) == 768
        assert article.embedding[1] == pytest.approx(FAKE_EMBEDDING[1])


def test_index_knowledge_base_defaults_to_synthetic_data_source(session):
    article = KnowledgeBaseArticle(title="Solo article", content="some content")
    session.add(article)
    session.commit()
    article_id = article.knowledge_base_article_id

    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(json_body={"embedding": FAKE_EMBEDDING})

        count = index_knowledge_base(session)

    assert count == 1
    session.expire_all()
    refreshed = session.get(KnowledgeBaseArticle, article_id)
    assert refreshed.embedding is not None
    assert len(refreshed.embedding) == 768


def test_index_knowledge_base_returns_zero_and_makes_no_calls_for_empty_corpus(session):
    with patch("app.ai.embeddings.httpx.post") as mock_post:
        count = index_knowledge_base(session)

    assert count == 0
    mock_post.assert_not_called()


def test_index_knowledge_base_does_not_commit_partial_work_on_embedding_failure(session):
    article = KnowledgeBaseArticle(title="Will fail", content="content")
    session.add(article)
    session.commit()
    article_id = article.knowledge_base_article_id

    with patch("app.ai.embeddings.httpx.post") as mock_post:
        mock_post.return_value = _mock_response(status_code=500, text="boom")

        with pytest.raises(EmbeddingRequestError):
            index_knowledge_base(session)

    session.rollback()
    session.expire_all()
    refreshed = session.get(KnowledgeBaseArticle, article_id)
    assert refreshed.embedding is None
