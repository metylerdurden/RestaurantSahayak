"""MemoryService: DineOps' persistent long-term memory subsystem (Constitution III).

    Agent -> (typed memory tools) -> MemoryService -> EmbeddingProvider (BGE-M3,
    embeddings only) + MemoryRepository -> PostgreSQL/pgvector

NOT RAG. There is no document, no chunk, no document-retrieval pipeline anywhere in
this file. A Memory row is one structured fact (a customer preference, a business
rule, a past decision, ...); `search_memory` finds facts by *meaning* rather than
exact topic match, which is a narrower, legitimate use of embeddings distinct from
RAG document retrieval. Classification (memory_type, topic, importance, confidence)
is the caller's job — in practice, Qwen3-8B's, via the agent that calls these
methods through typed tools — not this service's. This service's job is: turn
content into a vector (via EmbeddingProvider, never the reasoning LLM), enforce the
memory lifecycle (dedup on exact key conflict, access tracking, reinforcement,
forgetting), and persist/retrieve.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any, Literal

from app.core.telemetry import get_tracer, start_span
from app.embeddings.base import EmbeddingProvider
from app.models import Memory
from app.models.memory import MEMORY_SOURCES, MEMORY_TYPES
from app.repositories.memory_repo import MemoryRepository
from app.tools.base import ToolError

_tracer = get_tracer(__name__)

MemoryType = Literal[
    "CUSTOMER_PREFERENCE",
    "BUSINESS_RULE",
    "MANAGER_PREFERENCE",
    "PAST_DECISION",
    "OPERATIONAL_FACT",
    "AGENT_EXPERIENCE",
]
MemorySource = Literal["manager_stated", "agent_inferred", "system_derived"]


class MemoryService:
    def __init__(self, repo: MemoryRepository, embeddings: EmbeddingProvider) -> None:
        self.repo = repo
        self.embeddings = embeddings

    # --- lifecycle step 1-6: extract/classify/assign/embed/persist ---

    async def add_memory(
        self,
        *,
        restaurant_id: uuid.UUID,
        memory_type: str,
        topic: str,
        content: dict[str, Any],
        source: str,
        importance: int = 3,
        confidence: float = 1.0,
        customer_id: uuid.UUID | None = None,
        agent_name: str | None = None,
        source_agent_run_id: uuid.UUID | None = None,
    ) -> Memory:
        # Deliberately no `topic`/`content` span attributes anywhere in this module —
        # that is customer/operational content, not an identifier, and telemetry must
        # never carry it (see app.core.telemetry module docstring).
        with start_span(
            _tracer,
            "memory.write",
            memory_operation="add",
            restaurant_id=str(restaurant_id),
            memory_type=memory_type,
            customer_id=str(customer_id) if customer_id else None,
            agent_name=agent_name,
            importance=importance,
        ) as span:
            self._validate_type_and_source(memory_type, source)
            self._validate_importance_confidence(importance, confidence)

            embedding = await self._embed_one(self._embedding_text(topic, content))
            scope_key = self._scope_key(customer_id, agent_name)

            # Exact-key safety net: the same (restaurant, type, scope, topic) can only
            # have one active memory (enforced by a DB partial unique index). If the
            # caller re-states a fact under the same key, treat this as a correction —
            # deactivate the stale row and persist the new content as a fresh active
            # memory, preserving the old one as an inactive audit record rather than
            # raising a conflict.
            existing = await self.repo.get_active_by_scope_topic(
                restaurant_id=restaurant_id, memory_type=memory_type, scope_key=scope_key, topic=topic
            )
            corrected_existing = existing is not None
            if existing is not None:
                existing.is_active = False
                await self.repo.save(existing)

            memory = await self.repo.create(
                restaurant_id=restaurant_id,
                customer_id=customer_id,
                agent_name=agent_name,
                memory_type=memory_type,
                topic=topic,
                content=content,
                embedding=embedding,
                importance=importance,
                confidence=Decimal(str(round(confidence, 2))),
                source=source,
                source_agent_run_id=source_agent_run_id,
                is_active=True,
            )
            span.set_attribute("memory_id", str(memory.id))
            span.set_attribute("corrected_existing", corrected_existing)
            span.set_attribute("success", True)
            return memory

    # --- lifecycle step 7: retrieve relevant memories ---

    async def search_memory(
        self,
        *,
        restaurant_id: uuid.UUID,
        query: str,
        memory_type: str | None = None,
        customer_id: uuid.UUID | None = None,
        agent_name: str | None = None,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[tuple[Memory, float]]:
        # No `query` text as a span attribute — it is manager/agent-composed free
        # text that may itself reference customer specifics.
        with start_span(
            _tracer,
            "memory.search",
            memory_operation="search",
            restaurant_id=str(restaurant_id),
            memory_type=memory_type,
            customer_id=str(customer_id) if customer_id else None,
            agent_name=agent_name,
            top_k=top_k,
        ) as span:
            if memory_type is not None and memory_type not in MEMORY_TYPES:
                raise ToolError("invalid_memory_type", f"Unknown memory_type: {memory_type!r}")

            query_vector = await self._embed_one(query)
            rows = await self.repo.search_by_embedding(
                restaurant_id=restaurant_id,
                query_vector=query_vector,
                memory_type=memory_type,
                customer_id=customer_id,
                agent_name=agent_name,
                limit=top_k,
            )

            results: list[tuple[Memory, float]] = []
            for memory, distance in rows:
                similarity = 1.0 - distance  # BGE-M3 vectors are L2-normalized: cosine distance -> similarity
                if similarity < min_similarity:
                    continue
                await self.repo.touch_access(memory)
                results.append((memory, similarity))

            span.set_attribute("result_count", len(results))
            span.set_attribute("success", True)
            return results

    async def list_customer_memories(
        self, *, restaurant_id: uuid.UUID, customer_id: uuid.UUID, active_only: bool = True, limit: int = 50
    ) -> list[Memory]:
        """Plain (non-semantic) listing for the Manager API's
        `GET /customers/{id}/memories` — "everything remembered about this
        customer," not "what's most relevant to this query" (that's
        `search_memory`, which needs a query string an API list view doesn't have)."""
        with start_span(
            _tracer,
            "memory.search",
            memory_operation="list_by_customer",
            restaurant_id=str(restaurant_id),
            customer_id=str(customer_id),
        ) as span:
            memories = await self.repo.list_by_customer(
                restaurant_id, customer_id, active_only=active_only, limit=limit
            )
            span.set_attribute("result_count", len(memories))
            span.set_attribute("success", True)
            return memories

    async def get_memory(self, *, restaurant_id: uuid.UUID, memory_id: uuid.UUID, touch: bool = True) -> Memory:
        memory = self._require(await self.repo.get_by_id(memory_id), restaurant_id, memory_id)
        if touch:
            await self.repo.touch_access(memory)
        return memory

    # --- lifecycle step 8: correct existing memories ---

    async def update_memory(
        self,
        *,
        restaurant_id: uuid.UUID,
        memory_id: uuid.UUID,
        content: dict[str, Any] | None = None,
        topic: str | None = None,
        importance: int | None = None,
        confidence: float | None = None,
    ) -> Memory:
        with start_span(
            _tracer,
            "memory.write",
            memory_operation="update",
            restaurant_id=str(restaurant_id),
            memory_id=str(memory_id),
        ) as span:
            memory = self._require(await self.repo.get_by_id(memory_id), restaurant_id, memory_id)
            if not memory.is_active:
                raise ToolError("memory_inactive", "Cannot update a memory that has been forgotten")

            if importance is not None or confidence is not None:
                self._validate_importance_confidence(
                    importance if importance is not None else memory.importance,
                    confidence if confidence is not None else float(memory.confidence),
                )

            content_or_topic_changed = content is not None or topic is not None
            if content is not None:
                memory.content = content
            if topic is not None:
                memory.topic = topic
            if importance is not None:
                memory.importance = importance
            if confidence is not None:
                memory.confidence = Decimal(str(round(confidence, 2)))

            if content_or_topic_changed:
                memory.embedding = await self._embed_one(self._embedding_text(memory.topic, memory.content))

            saved = await self.repo.save(memory)
            span.set_attribute("memory_type", saved.memory_type)
            span.set_attribute("success", True)
            return saved

    # --- lifecycle step 9: reinforcement (and access tracking, shared with search/get) ---

    async def reinforce_memory(
        self, *, restaurant_id: uuid.UUID, memory_id: uuid.UUID, confidence_step: float = 0.1
    ) -> Memory:
        with start_span(
            _tracer,
            "memory.write",
            memory_operation="reinforce",
            restaurant_id=str(restaurant_id),
            memory_id=str(memory_id),
        ) as span:
            memory = self._require(await self.repo.get_by_id(memory_id), restaurant_id, memory_id)
            if not memory.is_active:
                raise ToolError("memory_inactive", "Cannot reinforce a memory that has been forgotten")

            new_confidence = min(1.0, float(memory.confidence) + confidence_step)
            memory.confidence = Decimal(str(round(new_confidence, 2)))
            await self.repo.touch_access(memory)
            saved = await self.repo.save(memory)
            span.set_attribute("confidence", float(saved.confidence))
            span.set_attribute("success", True)
            return saved

    # --- lifecycle step 10: let low-value memories be forgotten/deleted ---

    async def forget_memory(
        self, *, restaurant_id: uuid.UUID, memory_id: uuid.UUID, reason: str | None = None
    ) -> Memory:
        """Soft delete: deactivates the memory (excluded from search/active scope) but
        keeps the row as an audit record, and frees its (type, scope, topic) key for
        a new active memory."""
        with start_span(
            _tracer,
            "memory.write",
            memory_operation="forget",
            restaurant_id=str(restaurant_id),
            memory_id=str(memory_id),
        ) as span:
            memory = self._require(await self.repo.get_by_id(memory_id), restaurant_id, memory_id)
            memory.is_active = False
            saved = await self.repo.save(memory)
            span.set_attribute("success", True)
            return saved

    async def delete_memory(self, *, restaurant_id: uuid.UUID, memory_id: uuid.UUID) -> None:
        """Hard delete: permanently removes the row. Use forget_memory for the normal
        "this is no longer true" case — this is for genuine removal (e.g. recorded in
        error)."""
        with start_span(
            _tracer,
            "memory.write",
            memory_operation="delete",
            restaurant_id=str(restaurant_id),
            memory_id=str(memory_id),
        ) as span:
            memory = self._require(await self.repo.get_by_id(memory_id), restaurant_id, memory_id)
            await self.repo.delete(memory)
            span.set_attribute("success", True)

    # --- helpers ---

    def _require(self, memory: Memory | None, restaurant_id: uuid.UUID, memory_id: uuid.UUID) -> Memory:
        if memory is None or memory.restaurant_id != restaurant_id:
            raise ToolError("memory_not_found", f"No memory found with id {memory_id}")
        return memory

    def _validate_type_and_source(self, memory_type: str, source: str) -> None:
        if memory_type not in MEMORY_TYPES:
            raise ToolError("invalid_memory_type", f"Unknown memory_type: {memory_type!r}")
        if source not in MEMORY_SOURCES:
            raise ToolError("invalid_source", f"Unknown source: {source!r}")

    def _validate_importance_confidence(self, importance: int, confidence: float) -> None:
        if not (1 <= importance <= 5):
            raise ToolError("invalid_importance", "importance must be between 1 and 5")
        if not (0.0 <= confidence <= 1.0):
            raise ToolError("invalid_confidence", "confidence must be between 0.0 and 1.0")

    def _scope_key(self, customer_id: uuid.UUID | None, agent_name: str | None) -> str:
        if customer_id is not None:
            return str(customer_id)
        if agent_name is not None:
            return agent_name
        return "restaurant"

    def _embedding_text(self, topic: str, content: dict[str, Any]) -> str:
        text = content.get("text") if isinstance(content, dict) else None
        return f"{topic}: {text}" if text else f"{topic}: {content}"

    async def _embed_one(self, text: str) -> list[float]:
        # BGE-M3 inference is synchronous/CPU-bound (sentence-transformers) — run off
        # the event loop so one embedding call doesn't stall every other coroutine.
        vectors = await asyncio.to_thread(self.embeddings.embed, [text])
        return vectors[0]
