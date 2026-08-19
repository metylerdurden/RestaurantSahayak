"""Manager API (Step 19): read-only customer views, including the memories a
customer has recorded against them — makes the MemoryService's persistent recall
(Constitution III) visible to the manager, not just to agents."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.stack import build_agent_stack
from app.schemas.customer import CustomerDTO
from app.schemas.memory import MemoryDTO
from app.tools.base import ToolError

router = APIRouter(prefix="/api/v1", tags=["customers"])


@router.get("/customers", response_model=list[CustomerDTO])
async def list_customers(
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[CustomerDTO]:
    stack = build_agent_stack(session)
    customers = await stack.customer_service.list_customers(restaurant_id=restaurant_id)
    return [CustomerDTO.model_validate(c) for c in customers]


@router.get("/customers/{customer_id}", response_model=CustomerDTO)
async def get_customer(
    customer_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> CustomerDTO:
    stack = build_agent_stack(session)
    try:
        customers = await stack.customer_service.get_customer(
            restaurant_id=restaurant_id, customer_id=customer_id, query=None
        )
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return CustomerDTO.model_validate(customers[0])


@router.get("/customers/{customer_id}/memories", response_model=list[MemoryDTO])
async def get_customer_memories(
    customer_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[MemoryDTO]:
    stack = build_agent_stack(session)
    memories = await stack.memory_service.list_customer_memories(
        restaurant_id=restaurant_id, customer_id=customer_id
    )
    return [MemoryDTO.model_validate(m) for m in memories]
