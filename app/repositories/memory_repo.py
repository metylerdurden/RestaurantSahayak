from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import Memory
from app.models.base import _utcnow
from app.repositories.base import BaseRepository


class MemoryRepository(BaseRepository):
    async def create(self, **fields) -> Memory:
        memory = Memory(**fields)
        self.session.add(memory)
        await self.session.flush()
        return memory

    async def get_by_id(self, memory_id: uuid.UUID) -> Memory | None:
        return await self.session.get(Memory, memory_id)

    async def get_active_by_scope_topic(
        self, *, restaurant_id: uuid.UUID, memory_type: str, scope_key: str, topic: str
    ) -> Memory | None:
        stmt = select(Memory).where(
            Memory.restaurant_id == restaurant_id,
            Memory.memory_type == memory_type,
            Memory.scope_key == scope_key,
            Memory.topic == topic,
            Memory.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search_by_embedding(
        self,
        *,
        restaurant_id: uuid.UUID,
        query_vector: list[float],
        memory_type: str | None,
        customer_id: uuid.UUID | None,
        agent_name: str | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        distance = Memory.embedding.cosine_distance(query_vector)
        stmt = select(Memory, distance.label("distance")).where(
            Memory.restaurant_id == restaurant_id,
            Memory.is_active.is_(True),
            Memory.embedding.is_not(None),
        )
        if memory_type is not None:
            stmt = stmt.where(Memory.memory_type == memory_type)
        if customer_id is not None:
            stmt = stmt.where(Memory.customer_id == customer_id)
        if agent_name is not None:
            stmt = stmt.where(Memory.agent_name == agent_name)
        stmt = stmt.order_by(distance).limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def list_by_customer(
        self, restaurant_id: uuid.UUID, customer_id: uuid.UUID, *, active_only: bool = True, limit: int = 50
    ) -> list[Memory]:
        """A plain (non-semantic) listing — no embedding/query needed — for a
        dashboard panel showing everything remembered about one customer, as
        opposed to `search_by_embedding`'s "most relevant to this query" retrieval."""
        stmt = select(Memory).where(
            Memory.restaurant_id == restaurant_id, Memory.customer_id == customer_id
        )
        if active_only:
            stmt = stmt.where(Memory.is_active.is_(True))
        stmt = stmt.order_by(Memory.importance.desc(), Memory.updated_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def touch_access(self, memory: Memory) -> Memory:
        memory.access_count += 1
        memory.last_accessed_at = _utcnow()
        await self.session.flush()
        return memory

    async def save(self, memory: Memory) -> Memory:
        await self.session.flush()
        return memory

    async def delete(self, memory: Memory) -> None:
        await self.session.delete(memory)
        await self.session.flush()
