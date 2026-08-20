"""AgentActivityService: the read side of agent-run observability data, for the
Manager API's Agent Activity view (Step 19) — the counterpart to `AgentRunService`
(which only writes run/message records; see its own docstring) and modeled directly
on `WorkflowRunService`'s read-side pattern (join on shared ids rather than
duplicating any storage).

    AgentRun (id) -> list_by_correlation_id -> every run sharing that correlation
                  -> group by parent_run_id -> the trace tree:
                     Orchestrator -> Specialist -> Tool/Memory (as tool_result
                     messages) -> Result (outcome_summary)
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from app.models import AgentRun
from app.repositories.agent_run_repo import AgentRunRepository
from app.schemas.agent_run import AgentMessageDTO, AgentRunNode
from app.tools.base import ToolError


class AgentActivityService:
    def __init__(self, repo: AgentRunRepository) -> None:
        self.repo = repo

    async def get_run(self, run_id: uuid.UUID) -> AgentRun:
        run = await self.repo.get_by_id(run_id)
        if run is None:
            raise ToolError("agent_run_not_found", f"No agent run found with id {run_id}")
        return run

    async def list_recent(
        self, restaurant_id: uuid.UUID, *, agent_name: str | None = None, limit: int = 20
    ) -> list[AgentRun]:
        return await self.repo.list_recent(restaurant_id, agent_name=agent_name, limit=limit)

    async def get_trace(self, run_id: uuid.UUID) -> AgentRunNode:
        """The requested run as the root, with every run it (transitively)
        delegated to nested underneath via `parent_run_id` — a specialist run
        requested directly, with no children, is just as valid a (single-node)
        trace as an orchestrator run's full tree."""
        run = await self.get_run(run_id)
        related = await self.repo.list_by_correlation_id(run.correlation_id)

        children_by_parent: dict[uuid.UUID, list[AgentRun]] = defaultdict(list)
        for candidate in related:
            if candidate.parent_run_id is not None:
                children_by_parent[candidate.parent_run_id].append(candidate)

        return await self._build_node(run, children_by_parent)

    async def _build_node(self, run: AgentRun, children_by_parent: dict[uuid.UUID, list[AgentRun]]) -> AgentRunNode:
        messages = await self.repo.list_messages(run.id)
        children = [await self._build_node(child, children_by_parent) for child in children_by_parent.get(run.id, [])]
        return AgentRunNode(
            id=run.id,
            agent_name=run.agent_name,
            model_name=run.model_name,
            status=run.status,
            trigger_type=run.trigger_type,
            outcome_summary=run.outcome_summary,
            started_at=run.started_at,
            completed_at=run.completed_at,
            messages=[AgentMessageDTO.model_validate(m) for m in messages],
            children=children,
        )
