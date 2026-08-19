from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import WorkflowRun
from app.repositories.base import BaseRepository


class WorkflowRunRepository(BaseRepository):
    async def create(self, **fields) -> WorkflowRun:
        run = WorkflowRun(**fields)
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_by_id(self, workflow_run_id: uuid.UUID) -> WorkflowRun | None:
        return await self.session.get(WorkflowRun, workflow_run_id)

    async def list_recent(
        self, restaurant_id: uuid.UUID, *, workflow_type: str | None = None, limit: int = 20
    ) -> list[WorkflowRun]:
        stmt = select(WorkflowRun).where(WorkflowRun.restaurant_id == restaurant_id)
        if workflow_type is not None:
            stmt = stmt.where(WorkflowRun.workflow_type == workflow_type)
        stmt = stmt.order_by(WorkflowRun.started_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def save(self, run: WorkflowRun) -> WorkflowRun:
        await self.session.flush()
        return run
