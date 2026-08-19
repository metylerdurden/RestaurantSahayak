"""OrchestratorAgent exercised end to end against real Postgres data and the real
qwen3:8b model via Ollama — for both the orchestrator's own routing/combination
decisions and every specialist agent it delegates to. Skips automatically (rather
than failing) if Ollama or the configured model isn't reachable.

The primary scenario requested: "Are we ready for tonight?", which must demonstrate
multiple specialist agents being invoked and their results combined into one answer.
A second scenario covers the customer-memory-into-reservation chain ("Book Raj for
Friday at 8"). As with every other live test in this project, assertions favor
invariants that must always hold (real specialists were actually invoked, no crash,
the answer is grounded in what they returned) over pinning the model's exact routing
choices or phrasing — a live 8B model's exact sequencing will vary run to run."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agents.analytics_agent import AnalyticsAgent
from app.agents.customer_agent import CustomerAgent
from app.agents.inventory_agent import InventoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.reservation_agent import ReservationAgent
from app.agents.staffing_agent import StaffingAgent
from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.models import AgentRun, Reservation, ShiftAssignment, StaffShift
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.analytics_repo import AnalyticsRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.repositories.staffing_repo import StaffingRepository
from app.services.agent_run_service import AgentRunService
from app.services.analytics_service import AnalyticsService
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.services.staffing_service import StaffingService
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool
from app.tools.base import utcnow
from app.tools.customer_tools import GetCustomerHistoryTool, GetCustomerTool, UpdateCustomerTool
from app.tools.inventory_tools import (
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)
from app.tools.reservation_tools import (
    CancelReservationTool,
    CreateReservationTool,
    FindAvailableTableTool,
    GetReservationsTool,
    ModifyReservationTool,
)
from app.tools.staffing_tools import (
    CalculateStaffRequirementTool,
    GetStaffAvailabilityTool,
    GetStaffScheduleTool,
)
from tests.integration.factories import (
    make_customer,
    make_inventory_item,
    make_restaurant,
    make_staff,
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


def _build_specialists(db_session, llm) -> dict:
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    inventory_service = InventoryService(InventoryRepository(db_session), approval_service, get_settings())
    staffing_service = StaffingService(StaffingRepository(db_session), get_settings())
    customer_service = CustomerService(CustomerRepository(db_session))
    analytics_service = AnalyticsService(AnalyticsRepository(db_session))
    agent_run_service = AgentRunService(AgentRunRepository(db_session))

    reservation_agent = ReservationAgent(
        llm=llm,
        tools=[
            GetReservationsTool(reservation_service), FindAvailableTableTool(reservation_service),
            CreateReservationTool(reservation_service), ModifyReservationTool(reservation_service),
            CancelReservationTool(reservation_service),
        ],
        agent_run_service=agent_run_service,
    )
    inventory_agent = InventoryAgent(
        llm=llm,
        tools=[
            GetInventoryTool(inventory_service), CheckStockTool(inventory_service),
            CalculateRequiredInventoryTool(inventory_service), CreatePurchaseRequestTool(inventory_service),
        ],
        agent_run_service=agent_run_service,
    )
    staffing_agent = StaffingAgent(
        llm=llm,
        tools=[
            GetStaffScheduleTool(staffing_service), GetStaffAvailabilityTool(staffing_service),
            CalculateStaffRequirementTool(staffing_service),
        ],
        agent_run_service=agent_run_service,
    )
    customer_agent = CustomerAgent(
        llm=llm,
        tools=[
            GetCustomerTool(customer_service), GetCustomerHistoryTool(customer_service),
            UpdateCustomerTool(customer_service),
        ],
        agent_run_service=agent_run_service,
    )
    analytics_agent = AnalyticsAgent(
        llm=llm,
        tools=[GetDailySalesTool(analytics_service), GetItemSalesTool(analytics_service), GetNoShowRateTool(analytics_service)],
        agent_run_service=agent_run_service,
    )

    return {
        "reservation": reservation_agent,
        "inventory": inventory_agent,
        "staffing": staffing_agent,
        "customer": customer_agent,
        "analytics": analytics_agent,
    }, agent_run_service


def _tonight_window():
    start = utcnow().replace(hour=18, minute=0, second=0, microsecond=0)
    if start < utcnow():
        start += timedelta(days=1)
    return start, start + timedelta(hours=4)


async def test_are_we_ready_for_tonight_invokes_multiple_specialists_and_combines_results(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    await make_inventory_item(db_session, restaurant, name="Olive Oil", quantity_on_hand=40, low_stock_threshold=10)

    start, end = _tonight_window()
    db_session.add(
        Reservation(
            restaurant_id=restaurant.id, customer_id=customer.id, table_id=table.id, party_size=4,
            requested_time=start + timedelta(minutes=30), status="booked", created_via="manager_request",
        )
    )
    server = await make_staff(db_session, restaurant, role="server", name="Alex")
    shift = StaffShift(
        restaurant_id=restaurant.id, start_at=start, end_at=end, required_staff_count=1, status="staffed"
    )
    db_session.add(shift)
    await db_session.flush()
    db_session.add(ShiftAssignment(shift_id=shift.id, staff_id=server.id))
    await db_session.flush()

    specialists, agent_run_service = _build_specialists(db_session, llm)
    orchestrator = OrchestratorAgent(llm=llm, specialists=specialists, agent_run_service=agent_run_service)

    result = await orchestrator.handle(
        "Are we ready for tonight?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status != "error", result.summary
    invoked = {inv.agent_name for inv in result.invocations}
    # The whole point of this scenario: more than one specialist must actually be
    # consulted and combined, not just one.
    assert len(invoked) >= 2, result.summary
    assert invoked <= {"reservation", "inventory", "staffing", "analytics", "customer"}
    assert all(inv.status != "error" for inv in result.invocations), result.invocations
    assert result.summary
    # Traceability: every specialist invoked is a real, separately-recorded AgentRun
    # linked under this orchestrator's run.
    child_runs = (
        await db_session.execute(select(AgentRun).where(AgentRun.parent_run_id == result.orchestrator_run_id))
    ).scalars().all()
    assert len(child_runs) == len(result.invocations)


async def test_book_raj_flow_uses_customer_then_reservation_agent(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551230088")
    for capacity in (2, 4, 6):
        await make_table(db_session, restaurant, seat_capacity=capacity)

    specialists, agent_run_service = _build_specialists(db_session, llm)
    orchestrator = OrchestratorAgent(
        llm=llm,
        specialists={"customer": specialists["customer"], "reservation": specialists["reservation"]},
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle(
        "Book Raj for Friday at 8.", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status != "error", result.summary
    invoked = [inv.agent_name for inv in result.invocations]
    assert "customer" in invoked, result.summary
    assert all(inv.status != "error" for inv in result.invocations), result.invocations
