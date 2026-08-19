"""BackgroundWorkflow: the shared base every autonomous/scheduled workflow (Step 17)
is built on — mirrors ToolCallingAgent's role for specialist agents: it owns the
generic run-tracking machinery (WorkflowRun creation, status, timing, error
containment) so each concrete workflow only implements its own domain logic.

    Scheduler -> BackgroundWorkflow.run() -> WorkflowRun(status="running") ->
    _execute() [invokes one agent directly, or the Orchestrator for cross-domain
    work] -> WorkflowRun(status="completed"/"failed", final_result)

Every agent/tool call a workflow's _execute() causes shares this run's
correlation_id — passed through to agent.handle(..., correlation_id=...), which
Step 15 already threads down to every AgentRun and AgentMessage it creates. That is
what makes "which agent runs and tool calls did this workflow cause" answerable via
WorkflowRunService.get_detail() without this class duplicating that tracking.

A workflow must never call a Tool or Service directly — only an agent's
`.handle()` — so a background workflow can no more bypass ApprovalService's
high-impact gate than a manager-initiated request can: a gated tool call still
returns PendingApprovalOutput instead of mutating anything, exactly as always.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Literal

from app.core.logging import get_logger
from app.core.telemetry import get_tracer, start_span
from app.models import WorkflowRun
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.tools.base import utcnow

_logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class BackgroundWorkflow(ABC):
    workflow_type: str

    def __init__(self, *, workflow_run_repo: WorkflowRunRepository) -> None:
        self.workflow_run_repo = workflow_run_repo

    async def run(
        self, *, restaurant_id: uuid.UUID, triggered_by: Literal["scheduler", "manual"] = "manual"
    ) -> WorkflowRun:
        started_at = time.monotonic()
        correlation_id = uuid.uuid4()
        workflow_run = await self.workflow_run_repo.create(
            workflow_type=self.workflow_type,
            restaurant_id=restaurant_id,
            correlation_id=correlation_id,
            status="running",
            triggered_by=triggered_by,
        )
        log = _logger.bind(
            workflow_id=str(workflow_run.id),
            workflow_type=self.workflow_type,
            correlation_id=str(correlation_id),
        )
        log.info("workflow.started", triggered_by=triggered_by)

        with start_span(
            _tracer,
            "workflow.run",
            workflow_name=self.workflow_type,
            restaurant_id=str(restaurant_id),
            correlation_id=str(correlation_id),
            triggered_by=triggered_by,
        ) as span:
            try:
                result = await self._execute(restaurant_id=restaurant_id, correlation_id=correlation_id)
                workflow_run.status = "completed"
                workflow_run.final_result = result
            except Exception as exc:  # a workflow's own bug or an agent's failure must never crash the scheduler
                log.error("workflow.failed", error=str(exc), exc_info=True)
                workflow_run.status = "failed"
                workflow_run.error = str(exc)

            workflow_run.completed_at = utcnow()
            workflow_run = await self.workflow_run_repo.save(workflow_run)
            latency_ms = int((time.monotonic() - started_at) * 1000)
            log.info("workflow.finished", status=workflow_run.status, latency_ms=latency_ms)

            span.set_attribute("success", workflow_run.status == "completed")
            span.set_attribute("status", workflow_run.status)
            span.set_attribute("latency_ms", latency_ms)
            return workflow_run

    @abstractmethod
    async def _execute(self, *, restaurant_id: uuid.UUID, correlation_id: uuid.UUID) -> dict[str, Any]:
        """Do the actual work — invoke one agent directly, or the Orchestrator for
        work spanning more than one domain — and return a JSON-serializable summary
        to store as WorkflowRun.final_result."""
