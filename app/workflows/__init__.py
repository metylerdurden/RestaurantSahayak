"""Event-driven workflows: the layer between EventBus and an agent.

    Event -> EventBus -> Workflow -> Agent -> Tools -> Services -> Database

A workflow handler's only job is to turn an EventEnvelope into a natural-language
instruction and call exactly one agent's `.handle()` — it never touches another
agent's tools/services directly, and it never bypasses approval gating (the agent's
own tools still go through ApprovalService exactly as they would for a manager
request). See app.services.event_bus for the bus itself and app.workflows.registry
for how handlers get wired to it.
"""

from app.workflows.registry import register_default_workflows

__all__ = ["register_default_workflows"]
