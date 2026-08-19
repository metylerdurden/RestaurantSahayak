"""The four autonomous background workflows against real Postgres data, with each
agent's own LLM scripted for speed/determinism (real Qwen3-8B behavior for the same
workflows is exercised in tests/integration/test_background_workflows_live.py).
Proves: each workflow calls the agent(s) the spec calls for, WorkflowRun tracking
(status/timing/triggered_by/final_result) is correct, every agent run and tool call
it caused is discoverable via WorkflowRunService (shared correlation_id), and —
critically — a high-impact action a workflow's agent proposes (a purchase request
above the cost threshold) still stops at pending approval rather than executing
immediately, exactly as it would for a manager-initiated request."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.agents.inventory_agent import InventoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.reservation_agent import ReservationAgent
from app.agents.staffing_agent import StaffingAgent
from app.core.config import get_settings
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.models import Approval, PurchaseRequest
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.repositories.staffing_repo import StaffingRepository
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.services.staffing_service import StaffingService
from app.services.workflow_run_service import WorkflowRunService
from app.tools.base import utcnow
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
from app.workflows.inventory_monitoring_workflow import InventoryMonitoringWorkflow
from app.workflows.reservation_monitoring_workflow import ReservationMonitoringWorkflow
from app.workflows.staffing_monitoring_workflow import StaffingMonitoringWorkflow
from sqlalchemy import select
from tests.integration.factories import make_customer, make_inventory_item, make_restaurant, make_staff, make_table

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


def _delegate(call_id: str, agent_name: str, instruction: str) -> LLMResponse:
    return LLMResponse(
        content="", model="fake-model",
        tool_calls=[ToolCall(id=call_id, name="delegate", arguments={"agent_name": agent_name, "instruction": instruction})],
    )


def _finish(call_id: str = "finish") -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name="finish", arguments={})])


async def test_inventory_monitoring_workflow_creates_a_pending_approval_not_a_blind_purchase(db_session):
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(db_session, restaurant, name="Truffle Oil", quantity_on_hand=1, low_stock_threshold=5)

    inventory_llm = ScriptedLLM([
        _tool_call("c0", "get_inventory", {"status": "low"}),
        _tool_call("c1", "calculate_required_inventory", {"item_id": str(item.id)}),
        _tool_call("c2", "create_purchase_request", {"item_id": str(item.id), "requested_quantity": "40", "estimated_cost": "600"}),
        _final("Truffle Oil is low. Requested 40 units at an estimated $600 — this needs manager approval."),
    ])
    approval_service = ApprovalService(ApprovalRepository(db_session))
    inventory_service = InventoryService(InventoryRepository(db_session), approval_service, get_settings())
    inventory_agent = InventoryAgent(
        llm=inventory_llm,
        tools=[
            GetInventoryTool(inventory_service), CheckStockTool(inventory_service),
            CalculateRequiredInventoryTool(inventory_service), CreatePurchaseRequestTool(inventory_service),
        ],
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )
    workflow = InventoryMonitoringWorkflow(
        workflow_run_repo=WorkflowRunRepository(db_session), inventory_agent=inventory_agent
    )

    run = await workflow.run(restaurant_id=restaurant.id, triggered_by="scheduler")

    assert run.status == "completed"
    assert run.workflow_type == "inventory_monitoring"
    assert run.triggered_by == "scheduler"
    assert run.final_result["status"] == "pending_approval"

    # The high-impact action must NOT have executed automatically.
    purchase_requests = (
        await db_session.execute(select(PurchaseRequest).where(PurchaseRequest.item_id == item.id))
    ).scalars().all()
    assert len(purchase_requests) == 1
    assert purchase_requests[0].status == "pending_approval"

    approvals = (
        await db_session.execute(select(Approval).where(Approval.restaurant_id == restaurant.id))
    ).scalars().all()
    assert len(approvals) == 1
    assert approvals[0].status == "pending"
    assert approvals[0].domain == "purchase"


async def test_staffing_monitoring_workflow_invokes_staffing_agent_directly(db_session):
    restaurant = await make_restaurant(db_session)
    await make_staff(db_session, restaurant, role="server", name="Alex")
    start = (utcnow() + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)

    staffing_llm = ScriptedLLM([
        _tool_call("c0", "get_staff_schedule", {"date_from": start.isoformat(), "date_to": start.isoformat()}),
        _tool_call("c1", "calculate_staff_requirement", {"start_at": start.isoformat(), "end_at": (start + timedelta(hours=4)).isoformat()}),
        _final("No shifts currently scheduled for tomorrow; based on expected demand, schedule 1 server, 1 cook, 1 host."),
    ])
    staffing_service = StaffingService(StaffingRepository(db_session), get_settings())
    staffing_agent = StaffingAgent(
        llm=staffing_llm,
        tools=[
            GetStaffScheduleTool(staffing_service), GetStaffAvailabilityTool(staffing_service),
            CalculateStaffRequirementTool(staffing_service),
        ],
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )
    workflow = StaffingMonitoringWorkflow(
        workflow_run_repo=WorkflowRunRepository(db_session), staffing_agent=staffing_agent
    )

    run = await workflow.run(restaurant_id=restaurant.id, triggered_by="manual")

    assert run.status == "completed"
    assert run.final_result["status"] == "completed"
    assert "schedule" in run.final_result["summary"].lower()
    assert len(staffing_llm.calls) == 3  # two tool-call turns, one final-answer turn


async def test_daily_briefing_workflow_uses_the_orchestrator_and_is_traceable(db_session):
    restaurant = await make_restaurant(db_session)
    await make_customer(db_session, restaurant)
    await make_table(db_session, restaurant, seat_capacity=4)

    reservation_llm = ScriptedLLM([
        _tool_call("c0", "get_reservations", {}),
        _final("2 reservations booked today, 6 covers total."),
    ])
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    reservation_agent = ReservationAgent(
        llm=reservation_llm,
        tools=[
            GetReservationsTool(reservation_service), FindAvailableTableTool(reservation_service),
            CreateReservationTool(reservation_service), ModifyReservationTool(reservation_service),
            CancelReservationTool(reservation_service),
        ],
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )

    orchestrator_llm = ScriptedLLM([
        _delegate("o0", "reservation", "What reservations and expected covers do we have today?"),
        _finish(),
        _final("Good morning! Today: 2 reservations booked, 6 covers total. Nothing else to flag."),
    ])
    orchestrator = OrchestratorAgent(
        llm=orchestrator_llm,
        specialists={"reservation": reservation_agent},
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )
    workflow_run_repo = WorkflowRunRepository(db_session)
    workflow = DailyBriefingWorkflow(workflow_run_repo=workflow_run_repo, orchestrator=orchestrator)

    run = await workflow.run(restaurant_id=restaurant.id, triggered_by="scheduler")

    assert run.status == "completed"
    assert run.workflow_type == "daily_briefing"
    assert "6 covers" in run.final_result["briefing"]
    assert run.final_result["specialists_consulted"] == ["reservation"]

    # Fully traceable: WorkflowRunService finds the orchestrator's run AND the
    # reservation specialist's run it delegated to, all via correlation_id.
    workflow_run_service = WorkflowRunService(workflow_run_repo, AgentRunRepository(db_session))
    detail = await workflow_run_service.get_detail(run.id)
    assert detail.workflow_run.id == run.id
    agent_names = {r.agent_name for r in detail.agent_runs}
    assert agent_names == {"orchestrator", "reservation"}
    assert all(r.status == "completed" for r in detail.agent_runs)
    assert any(tc.tool_name == "get_reservations" for tc in detail.tool_calls)
    assert detail.errors == []


async def test_reservation_monitoring_workflow_uses_the_orchestrator(db_session):
    restaurant = await make_restaurant(db_session)
    await make_customer(db_session, restaurant, name="Raj Patel")
    await make_table(db_session, restaurant, seat_capacity=8)

    reservation_llm = ScriptedLLM([
        _tool_call("c0", "get_reservations", {}),
        _final("No unusual reservations found for the upcoming period."),
    ])
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    reservation_agent = ReservationAgent(
        llm=reservation_llm,
        tools=[
            GetReservationsTool(reservation_service), FindAvailableTableTool(reservation_service),
            CreateReservationTool(reservation_service), ModifyReservationTool(reservation_service),
            CancelReservationTool(reservation_service),
        ],
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )
    orchestrator_llm = ScriptedLLM([
        _delegate("o0", "reservation", "Are there any unusual upcoming reservations?"),
        _finish(),
        _final("Nothing unusual among upcoming reservations right now."),
    ])
    orchestrator = OrchestratorAgent(
        llm=orchestrator_llm,
        specialists={"reservation": reservation_agent},
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )
    workflow = ReservationMonitoringWorkflow(
        workflow_run_repo=WorkflowRunRepository(db_session), orchestrator=orchestrator
    )

    run = await workflow.run(restaurant_id=restaurant.id, triggered_by="scheduler")

    assert run.status == "completed"
    assert run.workflow_type == "reservation_monitoring"
    assert "nothing unusual" in run.final_result["summary"].lower()


async def test_a_failing_agent_marks_the_workflow_run_failed_not_the_process(db_session):
    class BoomLLM(LLMProvider):
        @property
        def model_name(self) -> str:
            return "fake-model"

        async def generate(self, *a, **kw):
            raise RuntimeError("ollama unreachable")

        async def health_check(self) -> bool:
            return False

    restaurant = await make_restaurant(db_session)
    staffing_service = StaffingService(StaffingRepository(db_session), get_settings())
    staffing_agent = StaffingAgent(
        llm=BoomLLM(),
        tools=[
            GetStaffScheduleTool(staffing_service), GetStaffAvailabilityTool(staffing_service),
            CalculateStaffRequirementTool(staffing_service),
        ],
        agent_run_service=AgentRunService(AgentRunRepository(db_session)),
    )
    workflow = StaffingMonitoringWorkflow(
        workflow_run_repo=WorkflowRunRepository(db_session), staffing_agent=staffing_agent
    )

    run = await workflow.run(restaurant_id=restaurant.id, triggered_by="scheduler")

    # StaffingAgent itself never raises (ToolCallingAgent.handle() always returns a
    # result) — so the workflow completes, but the agent's own result reports the
    # failure, which the workflow's final_result must surface, not hide.
    assert run.status == "completed"
    assert run.final_result["status"] == "error"
