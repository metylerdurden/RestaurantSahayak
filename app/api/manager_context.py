"""Helper for Manager API routes (Step 19) that call a domain service directly
(bypassing the LLM agent loop entirely — a manager clicking "cancel reservation" in
the dashboard shouldn't have to wait on Qwen3-8B) but still need every mutation
attributable to an AgentRun, because `ApprovalService.create_approval_request`'s
`proposed_by_agent_run_id` is a NOT NULL, RESTRICT foreign key — a high-impact
reservation change can't be proposed for approval without one.

Reuses `AgentRunService` exactly as any real agent does; `"manager_dashboard"` is
just another `agent_name` value, not a special case anywhere in the schema. A
welcome side effect: a manager's direct dashboard actions show up in Agent Activity
right alongside LLM-driven ones — one uniform audit trail for everything the system
does, with no change to the core agent architecture.
"""

from __future__ import annotations

import uuid
from typing import Literal

from app.models import AgentRun
from app.repositories.user_repo import UserRepository
from app.services.agent_run_service import AgentRunService
from app.tools.base import ToolContext, ToolError

MANAGER_AGENT_NAME = "manager_dashboard"


async def resolve_initiated_by_user_id(
    user_repo: UserRepository, *, restaurant_id: uuid.UUID, initiated_by_user_id: uuid.UUID | None
) -> uuid.UUID:
    """`AgentRun.trigger_type == "manager_request"` requires a non-null
    `initiated_by_user_id` (a real, enforced DB constraint — see
    `ck_agent_runs_trigger_consistency`). The frontend normally supplies the id it
    got from the dashboard's own `manager_user_id`, but every route that can reach
    this falls back to resolving one server-side rather than trusting every caller
    to remember to pass it."""
    if initiated_by_user_id is not None:
        return initiated_by_user_id
    manager = await user_repo.get_first(restaurant_id)
    if manager is None:
        raise ToolError(
            "no_manager_user_found",
            f"No user exists for restaurant {restaurant_id} to attribute this action to.",
        )
    return manager.id


async def start_manager_run(
    agent_run_service: AgentRunService,
    user_repo: UserRepository,
    *,
    restaurant_id: uuid.UUID,
    initiated_by_user_id: uuid.UUID | None = None,
) -> tuple[AgentRun, ToolContext]:
    resolved_user_id = await resolve_initiated_by_user_id(
        user_repo, restaurant_id=restaurant_id, initiated_by_user_id=initiated_by_user_id
    )
    run = await agent_run_service.start_run(
        restaurant_id=restaurant_id,
        agent_name=MANAGER_AGENT_NAME,
        model_name="n/a",
        trigger_type="manager_request",
        initiated_by_user_id=resolved_user_id,
    )
    context = ToolContext(
        restaurant_id=restaurant_id,
        correlation_id=str(run.correlation_id),
        acting_agent=MANAGER_AGENT_NAME,
        trigger_type="manager_request",
        agent_run_id=run.id,
    )
    return run, context


async def complete_manager_run(
    agent_run_service: AgentRunService,
    run: AgentRun,
    *,
    status: Literal["completed", "failed"],
    summary: str,
) -> None:
    await agent_run_service.complete_run(run_id=run.id, status=status, outcome_summary=summary)
