"""Manual trigger routes for autonomous background workflows (Step 17) — lets a
developer run any of the four workflows immediately, outside its normal schedule,
for testing.

    POST /workflows/{workflow_type}/trigger?restaurant_id=... -> runs it now,
        waits for it to finish, and returns the WorkflowRun record
    GET  /workflows/runs/{workflow_run_id} -> the run plus every agent run and
        tool call it caused
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.stack import build_agent_stack
from app.models.workflow_run import WORKFLOW_TYPES
from app.schemas.workflow_run import WorkflowRunDetail, WorkflowRunDTO
from app.tools.base import ToolError

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/{workflow_type}/trigger", response_model=WorkflowRunDTO)
async def trigger_workflow(
    workflow_type: str,
    restaurant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> WorkflowRunDTO:
    if workflow_type not in WORKFLOW_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown workflow_type: {workflow_type!r}")

    stack = build_agent_stack(session)
    workflow = stack.background_workflows[workflow_type]
    run = await workflow.run(restaurant_id=restaurant_id, triggered_by="manual")
    return WorkflowRunDTO.model_validate(run)


@router.get("/runs/{workflow_run_id}", response_model=WorkflowRunDetail)
async def get_workflow_run(
    workflow_run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> WorkflowRunDetail:
    stack = build_agent_stack(session)
    try:
        return await stack.workflow_run_service.get_detail(workflow_run_id)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
