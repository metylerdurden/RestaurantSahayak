"""DailyBriefingWorkflow: every morning, ask the Orchestrator to pull together
today's reservations/expected covers, inventory status, staffing, relevant
operational memories, and recent sales, and produce one manager-facing briefing.
Uses the Orchestrator (not a single agent directly) because this genuinely spans
every domain — exactly the "ask the appropriate specialists, combine results" job
the Orchestrator already exists to do.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agents.orchestrator_agent import OrchestratorAgent
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.workflows.background_workflow import BackgroundWorkflow

_BRIEFING_TASK = (
    "Prepare this morning's operational briefing for the manager. Cover, in one "
    "combined briefing: today's reservations and expected covers, current inventory "
    "status (anything low or worth restocking), today's staffing (adequately staffed "
    "or not), any relevant operational memories worth knowing about today, and — if "
    "it would add useful context — how recent sales have been trending. Keep it "
    "concise and actionable."
)


class DailyBriefingWorkflow(BackgroundWorkflow):
    workflow_type = "daily_briefing"

    def __init__(self, *, workflow_run_repo: WorkflowRunRepository, orchestrator: OrchestratorAgent) -> None:
        super().__init__(workflow_run_repo=workflow_run_repo)
        self.orchestrator = orchestrator

    async def _execute(self, *, restaurant_id: uuid.UUID, correlation_id: uuid.UUID) -> dict[str, Any]:
        result = await self.orchestrator.handle(
            _BRIEFING_TASK,
            restaurant_id=restaurant_id,
            trigger_type="scheduled",
            correlation_id=correlation_id,
        )
        return {
            "status": result.status,
            "briefing": result.summary,
            "specialists_consulted": [inv.agent_name for inv in result.invocations],
        }
