"""Manager API (Step 19): Agents / Agent Activity surface.

POST /api/v1/agent-runs             -> ask the orchestrator (or one named
                                        specialist) something, right now, and
                                        wait for the result
GET  /api/v1/agent-runs             -> recent runs for a restaurant
GET  /api/v1/agent-runs/{id}        -> one run's own record
GET  /api/v1/agent-runs/{id}/trace  -> the full nested trace for the Agent
                                        Activity view: Orchestrator -> Specialist
                                        -> Tool/Memory -> Result
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.manager_context import resolve_initiated_by_user_id
from app.api.stack import build_agent_stack
from app.schemas.agent_run import AgentRunDTO, AgentRunNode, TriggerAgentRunInput, TriggerAgentRunOutput
from app.schemas.workflow_run import AgentRunSummaryDTO
from app.tools.base import ToolError

router = APIRouter(prefix="/api/v1", tags=["agent-runs"])


@router.post("/agent-runs", response_model=TriggerAgentRunOutput)
async def trigger_agent_run(
    body: TriggerAgentRunInput,
    session: AsyncSession = Depends(get_db_session),
) -> TriggerAgentRunOutput:
    stack = build_agent_stack(session)
    try:
        initiated_by_user_id = await resolve_initiated_by_user_id(
            stack.user_repo, restaurant_id=body.restaurant_id, initiated_by_user_id=body.initiated_by_user_id
        )
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    if body.agent_name == "orchestrator":
        result = await stack.orchestrator.handle(
            body.task, restaurant_id=body.restaurant_id, initiated_by_user_id=initiated_by_user_id
        )
        return TriggerAgentRunOutput(
            agent_run_id=result.orchestrator_run_id,
            status=result.status,
            summary=result.summary,
            pending_approval_id=result.awaiting_approval_id,
        )

    specialist = getattr(stack, f"{body.agent_name}_agent")
    result = await specialist.handle(
        body.task, restaurant_id=body.restaurant_id, initiated_by_user_id=initiated_by_user_id
    )
    return TriggerAgentRunOutput(
        agent_run_id=result.agent_run_id,
        status=result.status,
        summary=result.summary,
        pending_approval_id=(result.data or {}).get("approval_id") if result.status == "pending_approval" else None,
    )


@router.get("/agent-runs", response_model=list[AgentRunSummaryDTO])
async def list_agent_runs(
    restaurant_id: uuid.UUID,
    agent_name: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentRunSummaryDTO]:
    stack = build_agent_stack(session)
    runs = await stack.agent_activity_service.list_recent(restaurant_id, agent_name=agent_name, limit=limit)
    return [AgentRunSummaryDTO.model_validate(r) for r in runs]


@router.get("/agent-runs/{run_id}", response_model=AgentRunDTO)
async def get_agent_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AgentRunDTO:
    stack = build_agent_stack(session)
    try:
        run = await stack.agent_activity_service.get_run(run_id)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return AgentRunDTO.model_validate(run)


@router.get("/agent-runs/{run_id}/trace", response_model=AgentRunNode)
async def get_agent_run_trace(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AgentRunNode:
    stack = build_agent_stack(session)
    try:
        return await stack.agent_activity_service.get_trace(run_id)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
