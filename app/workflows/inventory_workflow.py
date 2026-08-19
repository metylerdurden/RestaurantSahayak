"""Reactive Inventory workflows: react to operational events without the Inventory
Agent needing to know who published them, and without any other agent needing to
know the Inventory Agent exists. Each handler's whole job is to turn an event into a
natural-language instruction and hand it to exactly one agent's `.handle()` — the
same interface a manager uses, so nothing here is a shortcut around normal tool/
service/approval gating.

    reservation.created -> handle_reservation_created -> InventoryAgent checks
    whether stock can cover the added demand

    inventory.low -> handle_inventory_low -> InventoryAgent analyzes the shortage
    and creates a purchase request if warranted (ApprovalService gates that exactly
    as it would for a manager-initiated request — no special-casing here)
"""

from __future__ import annotations

from app.agents.inventory_agent import InventoryAgent
from app.schemas.event import EventEnvelope


async def handle_reservation_created(event: EventEnvelope, *, inventory_agent: InventoryAgent) -> None:
    party_size = event.payload.get("party_size", "an unspecified number of")
    requested_time = event.payload.get("requested_time", "an upcoming time")
    instruction = (
        f"A new reservation was just booked for {party_size} people at {requested_time}. "
        "Check whether current stock levels are sufficient for this additional demand, "
        "and flag anything that looks like it might run low."
    )
    await inventory_agent.handle(
        instruction,
        restaurant_id=event.restaurant_id,
        trigger_type="event",
        triggering_event_id=event.event_id,
    )


async def handle_inventory_low(event: EventEnvelope, *, inventory_agent: InventoryAgent) -> None:
    item_name = event.payload.get("item_name", "An inventory item")
    quantity_on_hand = event.payload.get("quantity_on_hand")
    threshold = event.payload.get("low_stock_threshold")
    instruction = (
        f"{item_name} is running low: {quantity_on_hand} on hand against a threshold of "
        f"{threshold}. Analyze how much is actually needed given recent usage, and place a "
        "purchase request if one is warranted."
    )
    await inventory_agent.handle(
        instruction,
        restaurant_id=event.restaurant_id,
        trigger_type="event",
        triggering_event_id=event.event_id,
    )
