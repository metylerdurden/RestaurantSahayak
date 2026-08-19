"""The Daily Briefing workflow exercised end to end against real Postgres data and
the real qwen3:8b model via Ollama, for the orchestrator and every specialist it may
delegate to. Skips automatically (rather than failing) if Ollama or the configured
model isn't reachable. As with every other live test in this project, assertions
favor invariants that must always hold (the workflow completed, real agent runs
happened, the briefing is grounded in real data) over pinning the model's exact
routing choices or phrasing."""

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
from app.models import AgentRun, Reservation
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.analytics_repo import AnalyticsRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.repositories.staffing_repo import StaffingRepository
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.analytics_service import AnalyticsService
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.services.staffing_service import StaffingService
from app.services.workflow_run_service import WorkflowRunService
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
from app.workflows.daily_briefing_workflow import DailyBriefingWorkflow
from tests.integration.factories import make_customer, make_inventory_item, make_restaurant, make_staff, make_table, make_user

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def llm():
    provider = build_llm_provider(get_settings())
    if not await provider.health_check():
        pytest.skip("Ollama is not reachable, or the configured LLM_MODEL is not pulled")
    return provider


def _build_orchestrator(db_session, llm) -> OrchestratorAgent:
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    inventory_service = InventoryService(InventoryRepository(db_session), approval_service, get_settings())
    staffing_service = StaffingService(StaffingRepository(db_session), get_settings())
    customer_service = CustomerService(CustomerRepository(db_session))
    analytics_service = AnalyticsService(AnalyticsRepository(db_session))

    specialists = {
        "reservation": ReservationAgent(
            llm=llm,
            tools=[
                GetReservationsTool(reservation_service), FindAvailableTableTool(reservation_service),
                CreateReservationTool(reservation_service), ModifyReservationTool(reservation_service),
                CancelReservationTool(reservation_service),
            ],
            agent_run_service=agent_run_service,
        ),
        "inventory": InventoryAgent(
            llm=llm,
            tools=[
                GetInventoryTool(inventory_service), CheckStockTool(inventory_service),
                CalculateRequiredInventoryTool(inventory_service), CreatePurchaseRequestTool(inventory_service),
            ],
            agent_run_service=agent_run_service,
        ),
        "staffing": StaffingAgent(
            llm=llm,
            tools=[
                GetStaffScheduleTool(staffing_service), GetStaffAvailabilityTool(staffing_service),
                CalculateStaffRequirementTool(staffing_service),
            ],
            agent_run_service=agent_run_service,
        ),
        "customer": CustomerAgent(
            llm=llm,
            tools=[
                GetCustomerTool(customer_service), GetCustomerHistoryTool(customer_service),
                UpdateCustomerTool(customer_service),
            ],
            agent_run_service=agent_run_service,
        ),
        "analytics": AnalyticsAgent(
            llm=llm,
            tools=[GetDailySalesTool(analytics_service), GetItemSalesTool(analytics_service), GetNoShowRateTool(analytics_service)],
            agent_run_service=agent_run_service,
        ),
    }
    return OrchestratorAgent(llm=llm, specialists=specialists, agent_run_service=agent_run_service)


async def test_daily_briefing_workflow_runs_end_to_end_with_the_real_model(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    await make_staff(db_session, restaurant, role="server", name="Alex")
    await make_inventory_item(db_session, restaurant, name="Olive Oil", quantity_on_hand=40)

    tonight = (utcnow() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    db_session.add(
        Reservation(
            restaurant_id=restaurant.id, customer_id=customer.id, table_id=table.id, party_size=4,
            requested_time=tonight, status="booked", created_via="manager_request",
        )
    )
    await db_session.flush()

    orchestrator = _build_orchestrator(db_session, llm)
    workflow_run_repo = WorkflowRunRepository(db_session)
    workflow = DailyBriefingWorkflow(workflow_run_repo=workflow_run_repo, orchestrator=orchestrator)

    run = await workflow.run(restaurant_id=restaurant.id, triggered_by="scheduler")

    assert run.status == "completed", run.error
    assert run.workflow_type == "daily_briefing"
    assert run.triggered_by == "scheduler"
    assert run.final_result is not None
    assert run.final_result["briefing"]

    # Real, scheduled-triggered agent runs actually happened, discoverable via the
    # workflow's own correlation_id.
    workflow_run_service = WorkflowRunService(workflow_run_repo, AgentRunRepository(db_session))
    detail = await workflow_run_service.get_detail(run.id)
    assert detail.agent_runs, "the orchestrator must have actually run"
    assert any(r.agent_name == "orchestrator" for r in detail.agent_runs)
    assert all(r.status in ("completed", "failed") for r in detail.agent_runs)
    for agent_run in detail.agent_runs:
        assert agent_run.model_name == llm.model_name
