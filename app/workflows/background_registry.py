"""Wires the four autonomous background workflows to real agents/services, and
optionally onto a Scheduler with default intervals. The one place that knows which
concrete agents each workflow needs — analogous to
app.services.approval_execution.build_executors and
app.workflows.registry.register_default_workflows for the reactive event workflows.

Interval-based, single-restaurant defaults: this MVP (like the rest of this
codebase — see scripts/seed.py) assumes one restaurant per deployment, and
AsyncIOScheduler is a fixed-interval scheduler, not calendar-aware — "daily_briefing"
here means "every DEFAULT_INTERVALS_SECONDS['daily_briefing']", not "every morning at
7am". A production scheduler behind the same Scheduler interface would add that.
"""

from __future__ import annotations

import uuid
from typing import Callable

from app.agents.inventory_agent import InventoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.staffing_agent import StaffingAgent
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.workflows.background_workflow import BackgroundWorkflow
from app.workflows.daily_briefing_workflow import DailyBriefingWorkflow
from app.workflows.inventory_monitoring_workflow import InventoryMonitoringWorkflow
from app.workflows.reservation_monitoring_workflow import ReservationMonitoringWorkflow
from app.workflows.scheduler import Scheduler
from app.workflows.staffing_monitoring_workflow import StaffingMonitoringWorkflow

DEFAULT_INTERVALS_SECONDS: dict[str, float] = {
    "daily_briefing": 24 * 60 * 60,
    "inventory_monitoring": 60 * 60,
    "reservation_monitoring": 30 * 60,
    "staffing_monitoring": 60 * 60,
}


def build_background_workflows(
    *,
    workflow_run_repo: WorkflowRunRepository,
    orchestrator: OrchestratorAgent,
    inventory_agent: InventoryAgent,
    staffing_agent: StaffingAgent,
) -> dict[str, BackgroundWorkflow]:
    return {
        "daily_briefing": DailyBriefingWorkflow(workflow_run_repo=workflow_run_repo, orchestrator=orchestrator),
        "inventory_monitoring": InventoryMonitoringWorkflow(
            workflow_run_repo=workflow_run_repo, inventory_agent=inventory_agent
        ),
        "reservation_monitoring": ReservationMonitoringWorkflow(
            workflow_run_repo=workflow_run_repo, orchestrator=orchestrator
        ),
        "staffing_monitoring": StaffingMonitoringWorkflow(
            workflow_run_repo=workflow_run_repo, staffing_agent=staffing_agent
        ),
    }


def register_background_workflows(
    scheduler: Scheduler,
    workflows: dict[str, BackgroundWorkflow],
    *,
    restaurant_id: uuid.UUID,
    intervals_seconds: dict[str, float] | None = None,
) -> None:
    intervals = intervals_seconds or DEFAULT_INTERVALS_SECONDS
    for workflow_type, workflow in workflows.items():
        interval = intervals.get(workflow_type, 60 * 60)

        def _make_job(w: BackgroundWorkflow) -> Callable:
            async def _job() -> None:
                await w.run(restaurant_id=restaurant_id, triggered_by="scheduler")

            return _job

        scheduler.register(workflow_type, interval_seconds=interval, func=_make_job(workflow))
