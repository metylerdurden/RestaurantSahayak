"""The event-driven pipeline exercised against the real qwen3:8b model via Ollama:
creating a reservation publishes reservation.created, which the Inventory workflow
reacts to by asking the real Inventory Agent to check whether stock can cover the
added demand. Skips automatically (rather than failing) if Ollama or the configured
model isn't reachable. As with every other live test in this project, assertions
favor invariants that must always hold (a real event-triggered agent run happened,
it used real tools against real data) over pinning the model's exact phrasing."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agents.inventory_agent import InventoryAgent
from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.models import AgentRun, Event
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
from tests.integration.factories import (
    future,
    make_agent_run,
    make_customer,
    make_inventory_item,
    make_restaurant,
    make_table,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def llm():
    provider = build_llm_provider(get_settings())
    if not await provider.health_check():
        pytest.skip("Ollama is not reachable, or the configured LLM_MODEL is not pulled")
    return provider


async def test_creating_a_reservation_triggers_a_real_inventory_agent_reaction(db_session, llm):
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    approval_service_for_inventory = ApprovalService(ApprovalRepository(db_session))
    inventory_service = InventoryService(
        InventoryRepository(db_session), approval_service_for_inventory, get_settings()
    )
    inventory_agent = InventoryAgent(
        llm=llm,
        tools=[
            GetInventoryTool(inventory_service),
            CheckStockTool(inventory_service),
            CalculateRequiredInventoryTool(inventory_service),
            CreatePurchaseRequestTool(inventory_service),
        ],
        agent_run_service=agent_run_service,
    )

    bus = InProcessEventBus(EventRepository(db_session))
    register_default_workflows(bus, inventory_agent=inventory_agent)

    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session),
        CustomerRepository(db_session),
        approval_service,
        get_settings(),
        event_bus=bus,
    )
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)
    await make_inventory_item(db_session, restaurant, name="Olive Oil", quantity_on_hand=40)
    await make_inventory_item(db_session, restaurant, name="Lamb", quantity_on_hand=15)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 6, "requested_time": future().isoformat()}, context=context
    )

    event = (await db_session.execute(select(Event).where(Event.entity_id == created.reservation.id))).scalar_one()
    assert event.event_type == "reservation.created"
    assert event.handled is True
    assert event.handler_results.get("inventory_workflow.handle_reservation_created") == "success", (
        event.handler_results
    )

    inventory_runs = (
        (
            await db_session.execute(
                select(AgentRun).where(AgentRun.agent_name == "inventory", AgentRun.triggering_event_id == event.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(inventory_runs) == 1
    run = inventory_runs[0]
    assert run.trigger_type == "event"
    assert run.status == "completed"
    assert run.outcome_summary  # the real model produced an actual answer, not nothing
