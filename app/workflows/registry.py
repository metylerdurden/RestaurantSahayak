"""Wires the default set of reactive workflows onto an EventBus. The one place that
knows which concrete agent reacts to which event type — analogous to
app.services.approval_execution.build_executors for the approval pipeline. Adding a
new reactive workflow means adding a subscribe() call here, not touching EventBus,
the publisher, or any agent.
"""

from __future__ import annotations

from functools import partial

from app.agents.inventory_agent import InventoryAgent
from app.services.event_bus import EventBus
from app.workflows.inventory_workflow import handle_inventory_low, handle_reservation_created


def register_default_workflows(bus: EventBus, *, inventory_agent: InventoryAgent | None = None) -> None:
    if inventory_agent is not None:
        bus.subscribe(
            "reservation.created",
            partial(handle_reservation_created, inventory_agent=inventory_agent),
            name="inventory_workflow.handle_reservation_created",
        )
        bus.subscribe(
            "inventory.low",
            partial(handle_inventory_low, inventory_agent=inventory_agent),
            name="inventory_workflow.handle_inventory_low",
        )
