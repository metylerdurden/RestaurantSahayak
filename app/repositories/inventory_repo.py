from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.models import InventoryItem, InventoryTransaction, PurchaseRequest
from app.repositories.base import BaseRepository


class InventoryRepository(BaseRepository):
    async def get_item(self, item_id: uuid.UUID) -> InventoryItem | None:
        return await self.session.get(InventoryItem, item_id)

    async def list_items(
        self,
        restaurant_id: uuid.UUID,
        *,
        status: str | None = None,
        name_contains: str | None = None,
    ) -> list[InventoryItem]:
        stmt = select(InventoryItem).where(
            InventoryItem.restaurant_id == restaurant_id, InventoryItem.is_active.is_(True)
        )
        if status is not None:
            stmt = stmt.where(InventoryItem.status == status)
        if name_contains is not None:
            stmt = stmt.where(InventoryItem.name.ilike(f"%{name_contains}%"))
        stmt = stmt.order_by(InventoryItem.name)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_transactions_since(self, item_id: uuid.UUID, since: datetime) -> list[InventoryTransaction]:
        stmt = (
            select(InventoryTransaction)
            .where(
                InventoryTransaction.item_id == item_id,
                InventoryTransaction.recorded_at >= since,
            )
            .order_by(InventoryTransaction.recorded_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_purchase_request(self, **fields) -> PurchaseRequest:
        purchase_request = PurchaseRequest(**fields)
        self.session.add(purchase_request)
        await self.session.flush()
        return purchase_request

    async def save_purchase_request(self, purchase_request: PurchaseRequest) -> PurchaseRequest:
        await self.session.flush()
        return purchase_request

    async def get_purchase_request(self, purchase_request_id: uuid.UUID) -> PurchaseRequest | None:
        return await self.session.get(PurchaseRequest, purchase_request_id)
