"""Shared constants for the knowledge base vector store.

Dependency-light module: only constants reused by the ORM model
(app.knowledge_base.models), the Dishka provider (app.providers.knowledge_base) and the
standalone batch scripts. It must NOT import repository/service/provider code, so it can be
imported anywhere without dragging in the dependency-injection layer. The actual langchain
objects (embedding client, PGEngine, PGVectorStore, HybridSearchConfig) are constructed by
the provider; the scripts resolve them from the app container.
"""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, UUID, Integer, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Index

from app.database import Base

TABLE_NAME = "knowledgebase"
EMBEDDING_DEPLOYMENT = "text-embedding-3-large"
VECTOR_SIZE = 3072

# Native hybrid search (langchain_postgres): the tsvector column holds the full-text on
# `content`; the vector + full-text fusion uses Reciprocal Rank Fusion (RRF) on the DB side.
TSV_COLUMN = "content_tsv"
TSV_INDEX = "knowledgebase_content_tsv_gin"
RRF_K = 60

# Metadata columns stored alongside each chunk (see app.knowledge_base.models.KnowledgeBase).
METADATA_NAMES = [
    "document_title",
    "category",
    "chunk_index",
    "permalink",
    "page_number",
]


class KnowledgeBase(Base):
    """ORM model for the `knowledgebase` table (managed by Alembic autogenerate).

    The schema mirrors 1:1 what langchain_postgres' PGVectorStore expects: it validates the
    table at runtime by column name + type (`langchain_id` UUID PK, `content` text,
    `embedding` pgvector, `content_tsv` tsvector for hybrid full-text search, plus metadata).
    It inherits directly from Base (not BaseTable/OkdEntity): the column set is dictated by
    langchain, so it has no standard id/timestamp/audit mixins.
    """

    __tablename__ = "knowledgebase"

    langchain_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(VECTOR_SIZE), nullable=False)
    # Populated DB-side by PGVectorStore on insert (to_tsvector over `content`).
    content_tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)

    document_title: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    permalink: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    langchain_metadata: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (Index(TSV_INDEX, "content_tsv", postgresql_using="gin"),)
