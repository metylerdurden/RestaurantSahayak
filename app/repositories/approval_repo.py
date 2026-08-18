from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.models import Approval
from app.repositories.base import BaseRepository


class ApprovalRepository(BaseRepository):
    async def create(self, **fields) -> Approval:
        approval = Approval(**fields)
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def get_by_id(self, approval_id: uuid.UUID) -> Approval | None:
        return await self.session.get(Approval, approval_id)

    async def list_pending(self, restaurant_id: uuid.UUID) -> list[Approval]:
        stmt = (
            select(Approval)
            .where(Approval.restaurant_id == restaurant_id, Approval.status == "pending")
            .order_by(Approval.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_overdue(self, restaurant_id: uuid.UUID, as_of: datetime) -> list[Approval]:
        stmt = select(Approval).where(
            Approval.restaurant_id == restaurant_id,
            Approval.status == "pending",
            Approval.expires_at.is_not(None),
            Approval.expires_at < as_of,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def save(self, approval: Approval) -> Approval:
        await self.session.flush()
        return approval
