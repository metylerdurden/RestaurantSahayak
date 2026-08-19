from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import AgentMessage, AgentRun
from app.repositories.base import BaseRepository


class AgentRunRepository(BaseRepository):
    async def create(self, **fields) -> AgentRun:
        run = AgentRun(**fields)
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_by_id(self, run_id: uuid.UUID) -> AgentRun | None:
        return await self.session.get(AgentRun, run_id)

    async def list_by_correlation_id(self, correlation_id: uuid.UUID) -> list[AgentRun]:
        stmt = (
            select(AgentRun)
            .where(AgentRun.correlation_id == correlation_id)
            .order_by(AgentRun.started_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_recent(
        self, restaurant_id: uuid.UUID, *, agent_name: str | None = None, limit: int = 20
    ) -> list[AgentRun]:
        stmt = select(AgentRun).where(AgentRun.restaurant_id == restaurant_id)
        if agent_name is not None:
            stmt = stmt.where(AgentRun.agent_name == agent_name)
        stmt = stmt.order_by(AgentRun.started_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def save(self, run: AgentRun) -> AgentRun:
        await self.session.flush()
        return run

    async def next_sequence_number(self, run_id: uuid.UUID) -> int:
        stmt = select(AgentMessage.sequence_number).where(AgentMessage.run_id == run_id)
        existing = (await self.session.execute(stmt)).scalars().all()
        return (max(existing) + 1) if existing else 1

    async def add_message(self, **fields) -> AgentMessage:
        message = AgentMessage(**fields)
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages(self, run_id: uuid.UUID) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.run_id == run_id)
            .order_by(AgentMessage.sequence_number)
        )
        return list((await self.session.execute(stmt)).scalars().all())
