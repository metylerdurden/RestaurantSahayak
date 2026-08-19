"""Manager API (Step 19): named triggers for the three autonomous background
workflows a manager would plausibly want to run on demand (the fourth,
`reservation_monitoring`, has no manager-facing button per this step's spec — it
stays reachable through the pre-existing generic `POST /workflows/{workflow_type}/
trigger`). Thin wrappers around the exact same `BackgroundWorkflow` instances the
scheduler uses (Step 17) — nothing about how a workflow runs changes here."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.stack import build_agent_stack
from app.schemas.workflow_run import WorkflowRunDTO

router = APIRouter(prefix="/api/v1/workflows", tags=["manager-workflows"])

_ROUTE_TO_WORKFLOW_TYPE = {
    "daily-briefing": "daily_briefing",
    "inventory-check": "inventory_monitoring",
    "staffing-check": "staffing_monitoring",
}


async def _trigger(workflow_type: str, restaurant_id: uuid.UUID, session: AsyncSession) -> WorkflowRunDTO:
    stack = build_agent_stack(session)
    workflow = stack.background_workflows[workflow_type]
    run = await workflow.run(restaurant_id=restaurant_id, triggered_by="manual")
    return WorkflowRunDTO.model_validate(run)


@router.post("/daily-briefing", response_model=WorkflowRunDTO)
async def trigger_daily_briefing(
    restaurant_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> WorkflowRunDTO:
    return await _trigger(_ROUTE_TO_WORKFLOW_TYPE["daily-briefing"], restaurant_id, session)


@router.post("/inventory-check", response_model=WorkflowRunDTO)
async def trigger_inventory_check(
    restaurant_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> WorkflowRunDTO:
    return await _trigger(_ROUTE_TO_WORKFLOW_TYPE["inventory-check"], restaurant_id, session)


@router.post("/staffing-check", response_model=WorkflowRunDTO)
async def trigger_staffing_check(
    restaurant_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> WorkflowRunDTO:
    return await _trigger(_ROUTE_TO_WORKFLOW_TYPE["staffing-check"], restaurant_id, session)
