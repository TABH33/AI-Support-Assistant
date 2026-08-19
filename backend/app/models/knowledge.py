"""KnowledgeBaseArticle model.

Holds the RAG corpus (troubleshooting guides / FAQs, Task 5) and its
pgvector embedding column (populated later by Task 11's indexing pipeline).
The embedding dimension (768) matches Ollama's `nomic-embed-text` model per
the Global Constraints.
"""

from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

EMBEDDING_DIMENSIONS = 768


class KnowledgeBaseArticle(Base):
    """A single knowledge-base article (troubleshooting guide / FAQ) used for RAG retrieval."""

    __tablename__ = "knowledge_base_articles"

    knowledge_base_article_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Nullable: rows exist before embedding (Task 5 seed) and are embedded afterwards (Task 11).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"KnowledgeBaseArticle(knowledge_base_article_id="
            f"{self.knowledge_base_article_id!r}, title={self.title!r})"
        )
