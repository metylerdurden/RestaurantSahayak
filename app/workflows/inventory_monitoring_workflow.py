"""InventoryMonitoringWorkflow: periodically ask the Inventory Agent to review
stock levels, work out expected demand and shortages using its own tools
(calculate_required_inventory already estimates demand from recent usage), and
place purchase requests where warranted. create_purchase_request already goes
through ApprovalService when the estimated cost is high-impact (Constitution IV) —
this workflow does not, and must not, second-guess or bypass that gate.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agents.inventory_agent import InventoryAgent
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.workflows.background_workflow import BackgroundWorkflow

_INVENTORY_MONITORING_TASK = (
    "Review current inventory. Identify anything low or out of stock, work out how "
    "much is actually needed given recent usage, and place purchase requests for "
    "anything that warrants reordering now. Report what you found and what you did."
)


class InventoryMonitoringWorkflow(BackgroundWorkflow):
    workflow_type = "inventory_monitoring"

    def __init__(self, *, workflow_run_repo: WorkflowRunRepository, inventory_agent: InventoryAgent) -> None:
        super().__init__(workflow_run_repo=workflow_run_repo)
        self.inventory_agent = inventory_agent

    async def _execute(self, *, restaurant_id: uuid.UUID, correlation_id: uuid.UUID) -> dict[str, Any]:
        result = await self.inventory_agent.handle(
            _INVENTORY_MONITORING_TASK,
            restaurant_id=restaurant_id,
            trigger_type="scheduled",
            correlation_id=correlation_id,
        )
        return {"status": result.status, "summary": result.summary}
