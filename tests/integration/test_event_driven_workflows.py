"""The event-driven pipeline end to end against real Postgres: publishing
reservation.created triggers the Inventory workflow, and publishing inventory.low
triggers the Inventory Agent's shortage-analysis-and-purchase-request workflow. The
Inventory Agent's own LLM is scripted here so this stays fast and deterministic
while still exercising the real Tool -> Service -> Repository -> DB stack; real
Qwen3-8B behavior for the same scenarios is exercised in
tests/integration/test_event_driven_workflows_live.py."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.inventory_agent import InventoryAgent
from app.core.config import get_settings
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.models import AgentRun, Event, PurchaseRequest
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.event_repo import EventRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.services.event_bus import InProcessEventBus
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.tools.base import ToolContext
from app.tools.inventory_tools import (
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)
from app.tools.reservation_tools import CreateReservationTool
from app.workflows import register_default_workflows
from tests.integration.factories import future, make_agent_run, make_customer, make_inventory_item, make_restaurant, make_table, make_user

pytestmark = pytest.mark.asyncio


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)

    async def health_check(self) -> bool:
        return True


def _tool_call(call_id: str, name: str, arguments: dict) -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake-model")


async def test_publishing_reservation_created_triggers_the_inventory_workflow(db_session):
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    inventory_service = InventoryService(
        InventoryRepository(db_session), ApprovalService(ApprovalRepository(db_session)), get_settings()
    )
    inventory_llm = ScriptedLLM([
        _tool_call("c0", "get_inventory", {}),
        _final("Stock looks sufficient for this additional demand."),
    ])
    inventory_agent = InventoryAgent(
        llm=inventory_llm,
        tools=[
            GetInventoryTool(inventory_service), CheckStockTool(inventory_service),
            CalculateRequiredInventoryTool(inventory_service), CreatePurchaseRequestTool(inventory_service),
        ],
        agent_run_service=agent_run_service,
    )

    bus = InProcessEventBus(EventRepository(db_session))
    register_default_workflows(bus, inventory_agent=inventory_agent)

    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service,
        get_settings(), event_bus=bus,
    )
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=4)
    await make_inventory_item(db_session, restaurant, name="Olive Oil")

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 4, "requested_time": future().isoformat()}, context=context
    )

    # The reservation.created event was persisted and marked handled...
    event = (
        await db_session.execute(select(Event).where(Event.entity_id == created.reservation.id))
    ).scalar_one()
    assert event.event_type == "reservation.created"
    assert event.handled is True
    assert event.handler_results == {"inventory_workflow.handle_reservation_created": "success"}

    # ...and the Inventory Agent genuinely ran in reaction to it: a real,
    # event-triggered AgentRun exists, linked back to this exact event.
    inventory_runs = (
        await db_session.execute(
            select(AgentRun).where(AgentRun.agent_name == "inventory", AgentRun.triggering_event_id == event.id)
        )
    ).scalars().all()
    assert len(inventory_runs) == 1
    assert inventory_runs[0].trigger_type == "event"
    assert inventory_runs[0].status == "completed"

    # And it actually did its job via a real tool call against real data, not a
    # no-op — the scripted call to get_inventory really executed.
    assert len(inventory_llm.calls) == 2  # one tool-call turn, one final-answer turn


async def test_publishing_inventory_low_triggers_a_purchase_recommendation(db_session):
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    approval_service = ApprovalService(ApprovalRepository(db_session))
    inventory_service = InventoryService(
        InventoryRepository(db_session), approval_service, get_settings()
    )
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(
        db_session, restaurant, name="Olive Oil", quantity_on_hand=2, low_stock_threshold=10
    )

    inventory_llm = ScriptedLLM([
        _tool_call("c0", "calculate_required_inventory", {"item_id": str(item.id)}),
        _tool_call("c1", "create_purchase_request", {"item_id": str(item.id), "requested_quantity": "30", "estimated_cost": "50"}),
        _final("Ordered 30 more units of Olive Oil to cover projected usage."),
    ])
    inventory_agent = InventoryAgent(
        llm=inventory_llm,
        tools=[
            GetInventoryTool(inventory_service), CheckStockTool(inventory_service),
            CalculateRequiredInventoryTool(inventory_service), CreatePurchaseRequestTool(inventory_service),
        ],
        agent_run_service=agent_run_service,
    )

    bus = InProcessEventBus(EventRepository(db_session))
    register_default_workflows(bus, inventory_agent=inventory_agent)

    envelope = await bus.publish(
        event_type="inventory.low",
        restaurant_id=restaurant.id,
        entity_id=item.id,
        payload={
            "item_id": str(item.id), "item_name": item.name,
            "quantity_on_hand": "2", "low_stock_threshold": "10",
        },
        published_by="test",
    )

    event = await db_session.get(Event, envelope.event_id)
    assert event.handler_results == {"inventory_workflow.handle_inventory_low": "success"}

    purchase_requests = (
        await db_session.execute(select(PurchaseRequest).where(PurchaseRequest.item_id == item.id))
    ).scalars().all()
    assert len(purchase_requests) == 1
    assert purchase_requests[0].requested_quantity == 30

    inventory_runs = (
        await db_session.execute(
            select(AgentRun).where(AgentRun.agent_name == "inventory", AgentRun.triggering_event_id == event.id)
        )
    ).scalars().all()
    assert len(inventory_runs) == 1
