"""AgentRunService: observability recording for agent executions (Constitution V/VI —
every agent action must be traceable: which agent ran, which model, what it was asked,
which tools it called with what results, and what it concluded).

This is infrastructure the agent orchestration code uses directly, not a typed tool —
the LLM never sees it in a `tools=[...]` list and cannot call it. It sits alongside
ApprovalService as a service domain agents/services depend on directly rather than
through the Agent -> Tool -> Service chain, since it is not restaurant operational
data an agent acts upon, but the record of the agent's own behavior.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from app.models import AgentMessage, AgentRun
from app.repositories.agent_run_repo import AgentRunRepository
from app.tools.base import ToolError, utcnow


class AgentRunService:
    def __init__(self, repo: AgentRunRepository) -> None:
        self.repo = repo

    async def start_run(
        self,
        *,
        restaurant_id: uuid.UUID,
        agent_name: str,
        model_name: str,
        trigger_type: Literal["manager_request", "event"],
        initiated_by_user_id: uuid.UUID | None = None,
        triggering_event_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
    ) -> AgentRun:
        """`correlation_id` lets a caller (OrchestratorAgent) thread one id across its
        own run and every specialist run it delegates to, so the whole multi-agent
        request is traceable as one unit via ix_agent_runs_correlation_id. Omitted by
        every other caller, which each still get a fresh one as before."""
        return await self.repo.create(
            restaurant_id=restaurant_id,
            agent_name=agent_name,
            model_name=model_name,
            parent_run_id=parent_run_id,
            correlation_id=correlation_id or uuid.uuid4(),
            trigger_type=trigger_type,
            initiated_by_user_id=initiated_by_user_id,
            triggering_event_id=triggering_event_id,
            status="running",
        )

    async def log_message(
        self,
        *,
        run_id: uuid.UUID,
        role: Literal["user", "assistant", "tool_call", "tool_result", "system"],
        content: dict[str, Any],
        tool_name: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> AgentMessage:
        sequence_number = await self.repo.next_sequence_number(run_id)
        return await self.repo.add_message(
            run_id=run_id,
            sequence_number=sequence_number,
            role=role,
            tool_name=tool_name,
            content=content,
            approval_id=approval_id,
        )

    async def complete_run(
        self,
        *,
        run_id: uuid.UUID,
        status: Literal["completed", "failed"],
        outcome_summary: str | None,
    ) -> AgentRun:
        run = await self.repo.get_by_id(run_id)
        if run is None:
            raise ToolError("agent_run_not_found", f"No agent run found with id {run_id}")
        run.status = status
        run.outcome_summary = outcome_summary
        run.completed_at = utcnow()
        return await self.repo.save(run)
