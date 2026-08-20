"""Memory: DineOps' persistent long-term memory subsystem (Constitution III).

NOT RAG: there is no document, no chunk, no document-retrieval pipeline here. A Memory
row is one structured fact DineOps has learned (a customer preference, a business
rule, a past decision, ...), addressed by (restaurant, memory_type, scope, topic).
The `embedding` column (pgvector) exists solely so MemoryService can do semantic
similarity search *over these structured facts* — e.g. "find memories related to
'seating preference'" without an exact topic match — which is a narrower, legitimate
use distinct from RAG document retrieval (see Constitution II and research.md §3).

Embedding dimension is 1024, matching BAAI/bge-m3 (app.embeddings.BGEEmbeddingProvider,
confirmed empirically in Phase 1's verify_providers.py run).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow

MEMORY_TYPES = (
    "CUSTOMER_PREFERENCE",
    "BUSINESS_RULE",
    "MANAGER_PREFERENCE",
    "PAST_DECISION",
    "OPERATIONAL_FACT",
    "AGENT_EXPERIENCE",
)
MEMORY_SOURCES = ("manager_stated", "agent_inferred", "system_derived")

EMBEDDING_DIMENSION = 1024  # BAAI/bge-m3


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ("
            "'CUSTOMER_PREFERENCE','BUSINESS_RULE','MANAGER_PREFERENCE',"
            "'PAST_DECISION','OPERATIONAL_FACT','AGENT_EXPERIENCE')",
            name="ck_memories_memory_type_valid",
        ),
        CheckConstraint(
            "source IN ('manager_stated','agent_inferred','system_derived')",
            name="ck_memories_source_valid",
        ),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_memories_importance_range"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_memories_confidence_range"
        ),
        # scope_key collapses the two "who/what is this about" columns into one
        # comparable value so the partial unique index below actually dedupes
        # correctly under NULLs (Postgres unique indexes treat NULL <> NULL, so a
        # naive multi-column unique constraint over nullable columns would silently
        # allow duplicate restaurant-wide/agent-wide facts).
        Index(
            "uq_memories_active_scope_topic",
            "restaurant_id",
            "memory_type",
            "scope_key",
            "topic",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_memories_restaurant_customer",
            "restaurant_id",
            "customer_id",
            postgresql_where=text("customer_id IS NOT NULL AND is_active"),
        ),
        Index(
            "ix_memories_restaurant_agent",
            "restaurant_id",
            "agent_name",
            postgresql_where=text("agent_name IS NOT NULL AND is_active"),
        ),
        Index("ix_memories_restaurant_type_active", "restaurant_id", "memory_type", "is_active"),
        # HNSW index for cosine-similarity search over embeddings (BGE-M3 vectors are
        # L2-normalized, so cosine distance is the correct metric).
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )

    # --- scope: who/what this memory is about ("where applicable" — both nullable) ---
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    # Named agent_name (not a literal agent_id FK): agents are code, not a persisted
    # entity/table — there is nothing for a real foreign key to point at. This holds
    # the same stable identifier AgentRun.agent_name uses (e.g. "reservation").
    agent_name: Mapped[str | None] = mapped_column(String, nullable=True)
    scope_key: Mapped[str] = mapped_column(
        String, Computed("COALESCE(customer_id::text, agent_name, 'restaurant')", persisted=True)
    )

    memory_type: Mapped[str] = mapped_column(String, nullable=False)
    topic: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=True)

    importance: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    # Numeric(3, 2) round-trips as Decimal, not float — memory_service.py already
    # treated it that way (Decimal(str(round(...))) on write, explicit float(...)
    # casts on read); this just makes the type hint match reality.
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("1.00"))
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
