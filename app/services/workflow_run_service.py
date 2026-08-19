"""WorkflowRunService: read-side assembly of the full record an autonomous workflow
run must expose — the run itself plus every agent run and tool call it caused,
found via the correlation_id every agent run it triggered shares (see
app.models.workflow_run.WorkflowRun's docstring), not duplicated storage.
"""

from __future__ import annotations

import uuid

from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.schemas.workflow_run import AgentRunSummaryDTO, ToolCallSummaryDTO, WorkflowRunDetail, WorkflowRunDTO
from app.tools.base import ToolError


class WorkflowRunService:
    def __init__(self, workflow_run_repo: WorkflowRunRepository, agent_run_repo: AgentRunRepository) -> None:
        self.workflow_run_repo = workflow_run_repo
        self.agent_run_repo = agent_run_repo

    async def get_detail(self, workflow_run_id: uuid.UUID) -> WorkflowRunDetail:
        workflow_run = await self.workflow_run_repo.get_by_id(workflow_run_id)
        if workflow_run is None:
            raise ToolError("workflow_run_not_found", f"No workflow run found with id {workflow_run_id}")

        agent_runs = await self.agent_run_repo.list_by_correlation_id(workflow_run.correlation_id)

        tool_calls: list[ToolCallSummaryDTO] = []
        errors: list[str] = []
        if workflow_run.error:
            errors.append(workflow_run.error)

        for run in agent_runs:
            if run.status == "failed" and run.outcome_summary:
                errors.append(f"{run.agent_name}: {run.outcome_summary}")
            for message in await self.agent_run_repo.list_messages(run.id):
                if message.role != "tool_result":
                    continue
                tool_calls.append(
                    ToolCallSummaryDTO(agent_run_id=run.id, tool_name=message.tool_name, content=message.content)
                )
                if isinstance(message.content, dict) and "error" in message.content:
                    errors.append(f"{run.agent_name}.{message.tool_name}: {message.content['error']}")

        return WorkflowRunDetail(
            workflow_run=WorkflowRunDTO.model_validate(workflow_run),
            agent_runs=[AgentRunSummaryDTO.model_validate(r) for r in agent_runs],
            tool_calls=tool_calls,
            errors=errors,
        )

    async def list_recent(self, restaurant_id: uuid.UUID, *, workflow_type: str | None = None, limit: int = 20):
        return await self.workflow_run_repo.list_recent(restaurant_id, workflow_type=workflow_type, limit=limit)
