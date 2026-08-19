"""StaffingMonitoringWorkflow: periodically ask the Staffing Agent to check whether
upcoming shifts are adequately staffed for expected demand. calculate_staff_requirement
already derives expected covers from real upcoming reservations when not given an
explicit number (Phase 7), so the Staffing Agent alone can genuinely "compare
upcoming reservations with staff schedules" without needing another agent's help —
this workflow invokes it directly rather than going through the Orchestrator.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agents.staffing_agent import StaffingAgent
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.workflows.background_workflow import BackgroundWorkflow

_STAFFING_MONITORING_TASK = (
    "Check whether upcoming shifts are adequately staffed given expected reservations. "
    "For each shift you look at, compare who's scheduled against what's actually "
    "required, and flag any shortage clearly with how many more of which role are "
    "needed."
)


class StaffingMonitoringWorkflow(BackgroundWorkflow):
    workflow_type = "staffing_monitoring"

    def __init__(self, *, workflow_run_repo: WorkflowRunRepository, staffing_agent: StaffingAgent) -> None:
        super().__init__(workflow_run_repo=workflow_run_repo)
        self.staffing_agent = staffing_agent

    async def _execute(self, *, restaurant_id: uuid.UUID, correlation_id: uuid.UUID) -> dict[str, Any]:
        result = await self.staffing_agent.handle(
            _STAFFING_MONITORING_TASK,
            restaurant_id=restaurant_id,
            trigger_type="scheduled",
            correlation_id=correlation_id,
        )
        return {"status": result.status, "summary": result.summary}
