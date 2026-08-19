"""Manager API (Step 19): read-only inventory views (no mutation endpoints are
requested for this API — write access stays through the Inventory Agent's own
tools/approval-gated purchase requests)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.stack import build_agent_stack
from app.schemas.inventory import InventoryItemDTO

router = APIRouter(prefix="/api/v1", tags=["inventory"])


@router.get("/inventory", response_model=list[InventoryItemDTO])
async def list_inventory(
    restaurant_id: uuid.UUID,
    status: str | None = None,
    name_contains: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[InventoryItemDTO]:
    stack = build_agent_stack(session)
    items = await stack.inventory_service.get_inventory(
        restaurant_id=restaurant_id, status=status, name_contains=name_contains
    )
    return [InventoryItemDTO.model_validate(i) for i in items]


@router.get("/inventory/alerts", response_model=list[InventoryItemDTO])
async def get_inventory_alerts(
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[InventoryItemDTO]:
    stack = build_agent_stack(session)
    out_of_stock = await stack.inventory_service.get_inventory(
        restaurant_id=restaurant_id, status="out_of_stock", name_contains=None
    )
    low = await stack.inventory_service.get_inventory(restaurant_id=restaurant_id, status="low", name_contains=None)
    return [InventoryItemDTO.model_validate(i) for i in [*out_of_stock, *low]]
