"""ReservationMonitoringWorkflow: periodically review upcoming reservations for
anything unusual, and identify VIP/returning customers among them using what's
already known about them. Uses the Orchestrator (not the Reservation Agent alone)
because "identify VIP/returning customers... use persistent memory where
appropriate" is Customer Agent territory — this genuinely spans two domains.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agents.orchestrator_agent import OrchestratorAgent
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.workflows.background_workflow import BackgroundWorkflow

_RESERVATION_MONITORING_TASK = (
    "Review upcoming reservations. Flag anything unusual — an unusually large party, "
    "a cluster of bookings at the same time, a last-minute booking, anything that "
    "doesn't look routine. Among these reservations, identify any customers who are "
    "VIPs or notable returning guests based on what's already known about them, and "
    "note anything relevant we should keep in mind for their visit."
)


class ReservationMonitoringWorkflow(BackgroundWorkflow):
    workflow_type = "reservation_monitoring"

    def __init__(self, *, workflow_run_repo: WorkflowRunRepository, orchestrator: OrchestratorAgent) -> None:
        super().__init__(workflow_run_repo=workflow_run_repo)
        self.orchestrator = orchestrator

    async def _execute(self, *, restaurant_id: uuid.UUID, correlation_id: uuid.UUID) -> dict[str, Any]:
        result = await self.orchestrator.handle(
            _RESERVATION_MONITORING_TASK,
            restaurant_id=restaurant_id,
            trigger_type="scheduled",
            correlation_id=correlation_id,
        )
        return {
            "status": result.status,
            "summary": result.summary,
            "specialists_consulted": [inv.agent_name for inv in result.invocations],
        }
