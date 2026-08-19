"""Runnable knowledge-base indexing script (Task 11): ``python -m app.ai.index_kb``.

Reads every `KnowledgeBaseArticle` row via the Task 10 `TelematicsDataSource`
abstraction (never a raw `db.query(KnowledgeBaseArticle)` here -- consistent
with the plan's intent that Tasks 11-16 depend on the interface, not
`app.models`/`Session` directly), embeds each article's `content` via
`app.ai.embeddings.embed_text` (Ollama), and writes the resulting vector into
the `embedding` pgvector column added in Task 4.

Mirrors `app.seed.seed`'s runnable-script shape: build a `SessionLocal()`
session, do the work, commit, print a summary, roll back and re-raise on
failure so a partially-run script never leaves a half-committed corpus.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.embeddings import embed_text
from app.database import SessionLocal
from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource


def index_knowledge_base(db: Session, data_source: TelematicsDataSource | None = None) -> int:
    """Embed every knowledge-base article's content and write it into the
    `embedding` column, via `data_source` (a `SyntheticDataSource(db)` by
    default). Returns the number of articles embedded.

    Commits once, after every article has been embedded successfully: if
    `embed_text` raises partway through (e.g. Ollama unreachable), nothing
    written so far is committed -- the caller sees the exception and the DB
    is left as it was before this call, rather than holding a
    partially-embedded corpus.
    """
    ds = data_source if data_source is not None else SyntheticDataSource(db)
    articles = ds.get_knowledge_base_articles()

    for article in articles:
        article.embedding = embed_text(article.content)

    db.commit()
    return len(articles)


def run() -> int:
    """Entry point for ``python -m app.ai.index_kb``: opens a real DB
    session, runs `index_knowledge_base`, and prints a summary."""
    session = SessionLocal()
    try:
        count = index_knowledge_base(session)
        print(f"Indexed {count} knowledge base article(s).")
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
