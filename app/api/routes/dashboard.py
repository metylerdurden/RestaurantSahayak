"""Manager API (Step 19): GET /api/v1/dashboard — the one-screen operational
snapshot: today's reservations/expected covers, inventory + staffing alerts,
pending approvals, recent agent activity, the latest daily briefing, and recent
operational events. All assembled by DashboardService from existing services; no
LLM call happens on this request path."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.stack import build_agent_stack
from app.schemas.dashboard import DashboardResponse

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> DashboardResponse:
    stack = build_agent_stack(session)
    return await stack.dashboard_service.get_dashboard(restaurant_id)
