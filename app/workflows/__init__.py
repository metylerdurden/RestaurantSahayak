"""Two kinds of workflow live here, both sitting between the rest of the system and
an agent:

Reactive (Step 16):    Event -> EventBus -> Workflow -> Agent -> Tools -> Services -> DB
Autonomous (Step 17):  Scheduler -> BackgroundWorkflow -> Agent(s) -> Tools -> Services -> DB

Either way, a workflow's only job is turning something (an event, a scheduled tick)
into a natural-language instruction and calling an agent's `.handle()` — it never
touches another agent's tools/services directly, and it never bypasses approval
gating (a gated tool call still returns PendingApprovalOutput instead of mutating
anything, exactly as it would for a manager request).
"""

from app.workflows.background_registry import build_background_workflows, register_background_workflows
from app.workflows.background_workflow import BackgroundWorkflow
from app.workflows.registry import register_default_workflows
from app.workflows.scheduler import AsyncIOScheduler, Scheduler

__all__ = [
    "register_default_workflows",
    "BackgroundWorkflow",
    "build_background_workflows",
    "register_background_workflows",
    "Scheduler",
    "AsyncIOScheduler",
]
