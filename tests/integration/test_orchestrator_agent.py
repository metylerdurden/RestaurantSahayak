"""OrchestratorAgent against real Postgres data and real specialist agents (real
services/tools/repositories — the full Agent -> Tool -> Service -> Repository -> DB
stack for each specialist), with every LLM call (the orchestrator's own, and each
specialist's) scripted rather than a real model. This proves the whole multi-agent
plumbing — AgentRun parent linkage, shared correlation_id, real tool execution
against real data, result combination — deterministically and fast. Real Qwen3-8B
routing/reasoning is exercised in tests/integration/test_orchestrator_agent_live.py."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agents.customer_agent import CustomerAgent
from app.agents.inventory_agent import InventoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.reservation_agent import ReservationAgent
from app.agents.staffing_agent import StaffingAgent
from app.core.config import get_settings
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.models import AgentRun, Reservation, ShiftAssignment, StaffShift
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.repositories.staffing_repo import StaffingRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.services.staffing_service import StaffingService
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
    return LLMResponse(
        content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)]
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake-model")


def _delegate(call_id: str, agent_name: str, instruction: str) -> LLMResponse:
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[
            ToolCall(id=call_id, name="delegate", arguments={"agent_name": agent_name, "instruction": instruction})
        ],
    )


def _finish(call_id: str = "finish") -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name="finish", arguments={})])


def _identify_domains(*domains: str) -> LLMResponse:
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[ToolCall(id="scope", name="identify_required_domains", arguments={"domains": list(domains)})],
    )


async def test_readiness_check_combines_reservation_inventory_and_staffing(db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    await make_inventory_item(db_session, restaurant, name="Olive Oil", quantity_on_hand=40, low_stock_threshold=10)

    tonight_start = (utcnow()).replace(hour=18, minute=0, second=0, microsecond=0)
    tonight_end = tonight_start + timedelta(hours=4)
    if tonight_start < utcnow():
        tonight_start += timedelta(days=1)
        tonight_end += timedelta(days=1)

    db_session.add(
        Reservation(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            table_id=table.id,
            party_size=4,
            requested_time=tonight_start + timedelta(minutes=30),
            status="booked",
            created_via="manager_request",
        )
    )
    server = await make_staff(db_session, restaurant, role="server", name="Alex")
    shift = StaffShift(
        restaurant_id=restaurant.id,
        start_at=tonight_start,
        end_at=tonight_end,
        required_staff_count=1,
        status="staffed",
    )
    db_session.add(shift)
    await db_session.flush()
    db_session.add(ShiftAssignment(shift_id=shift.id, staff_id=server.id))
    await db_session.flush()

    # --- real specialist agents, real DB-backed services, scripted LLMs ---
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    inventory_service = InventoryService(InventoryRepository(db_session), approval_service, get_settings())
    staffing_service = StaffingService(StaffingRepository(db_session), get_settings())
    agent_run_service = AgentRunService(AgentRunRepository(db_session))

    reservation_llm = ScriptedLLM(
        [
            _tool_call("c0", "get_reservations", {}),
            _final("There are 4 covers booked tonight across 1 reservation."),
        ]
    )
    reservation_agent = ReservationAgent(
        llm=reservation_llm,
        tools=[
            GetReservationsTool(reservation_service),
            FindAvailableTableTool(reservation_service),
            CreateReservationTool(reservation_service),
            ModifyReservationTool(reservation_service),
            CancelReservationTool(reservation_service),
        ],
        agent_run_service=agent_run_service,
    )

    inventory_llm = ScriptedLLM(
        [
            _tool_call("c0", "get_inventory", {}),
            _final("All inventory items are adequately stocked, including Olive Oil at 40 liters."),
        ]
    )
    inventory_agent = InventoryAgent(
        llm=inventory_llm,
        tools=[
            GetInventoryTool(inventory_service),
            CheckStockTool(inventory_service),
            CalculateRequiredInventoryTool(inventory_service),
            CreatePurchaseRequestTool(inventory_service),
        ],
        agent_run_service=agent_run_service,
    )

    staffing_llm = ScriptedLLM(
        [
            _tool_call(
                "c0",
                "get_staff_schedule",
                {"date_from": tonight_start.isoformat(), "date_to": tonight_start.isoformat()},
            ),
            _final("Tonight's shift has 1 server scheduled, which meets the requirement."),
        ]
    )
    staffing_agent = StaffingAgent(
        llm=staffing_llm,
        tools=[
            GetStaffScheduleTool(staffing_service),
            GetStaffAvailabilityTool(staffing_service),
            CalculateStaffRequirementTool(staffing_service),
        ],
        agent_run_service=agent_run_service,
    )

    orchestrator_llm = ScriptedLLM(
        [
            _identify_domains("reservation", "inventory", "staffing"),
            _delegate("o0", "reservation", "What reservations are booked for tonight?"),
            _delegate("o1", "inventory", "Is inventory in good shape for tonight?"),
            _delegate("o2", "staffing", "Are we staffed for tonight's shift?"),
            _finish(),
            _final(
                "Tonight looks ready: 4 covers booked, inventory (including Olive Oil) is well stocked, "
                "and the shift is staffed with 1 server as required."
            ),
        ]
    )
    orchestrator = OrchestratorAgent(
        llm=orchestrator_llm,
        specialists={"reservation": reservation_agent, "inventory": inventory_agent, "staffing": staffing_agent},
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle(
        "Are we ready for tonight?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status == "completed"
    assert [inv.agent_name for inv in result.invocations] == ["reservation", "inventory", "staffing"]
    assert "4 covers" in result.summary
    assert "Olive Oil" in result.summary or "stocked" in result.summary
    assert "staffed" in result.summary

    # AgentRun hierarchy: every specialist run should be parented under the
    # orchestrator's own run, and share its correlation_id, so the whole request is
    # traceable as one unit.
    run_repo = AgentRunRepository(db_session)
    orchestrator_run = await run_repo.get_by_id(result.orchestrator_run_id)
    assert orchestrator_run is not None
    child_runs = (
        (await db_session.execute(select(AgentRun).where(AgentRun.parent_run_id == result.orchestrator_run_id)))
        .scalars()
        .all()
    )
    assert len(child_runs) == 3
    assert {r.agent_name for r in child_runs} == {"reservation", "inventory", "staffing"}
    assert all(r.correlation_id == orchestrator_run.correlation_id for r in child_runs)


async def test_book_raj_flow_chains_customer_memory_into_reservation(db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551230099")
    await make_table(db_session, restaurant, seat_capacity=4)

    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    customer_service = CustomerService(CustomerRepository(db_session))
    agent_run_service = AgentRunService(AgentRunRepository(db_session))

    customer_llm = ScriptedLLM(
        [
            _tool_call("c0", "get_customer", {"query": "Raj"}),
            _final(f"Found Raj Patel (customer_id={raj.id}). No specific seating preference is on file."),
        ]
    )
    customer_agent = CustomerAgent(
        llm=customer_llm,
        tools=[
            GetCustomerTool(customer_service),
            GetCustomerHistoryTool(customer_service),
            UpdateCustomerTool(customer_service),
        ],
        agent_run_service=agent_run_service,
    )

    friday = utcnow() + timedelta(days=(4 - utcnow().weekday()) % 7 + 1)
    friday_8pm = friday.replace(hour=20, minute=0, second=0, microsecond=0)

    reservation_llm = ScriptedLLM(
        [
            _tool_call(
                "c0",
                "create_reservation",
                {"customer_id": str(raj.id), "party_size": 2, "requested_time": friday_8pm.isoformat()},
            ),
            _final("Booked Raj Patel a table for Friday at 8pm."),
        ]
    )
    reservation_agent = ReservationAgent(
        llm=reservation_llm,
        tools=[
            GetReservationsTool(reservation_service),
            FindAvailableTableTool(reservation_service),
            CreateReservationTool(reservation_service),
            ModifyReservationTool(reservation_service),
            CancelReservationTool(reservation_service),
        ],
        agent_run_service=agent_run_service,
    )

    orchestrator_llm = ScriptedLLM(
        [
            _identify_domains("customer", "reservation"),
            _delegate("o0", "customer", "Look up Raj and any known preferences."),
            _delegate(
                "o1",
                "reservation",
                f"Book a table for customer_id={raj.id} (Raj Patel) for Friday at 8pm, party of 2.",
            ),
            _finish(),
            _final("Booked Raj Patel a table for Friday at 8pm."),
        ]
    )
    orchestrator = OrchestratorAgent(
        llm=orchestrator_llm,
        specialists={"customer": customer_agent, "reservation": reservation_agent},
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle(
        "Book Raj for Friday at 8.", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status == "completed"
    assert [inv.agent_name for inv in result.invocations] == ["customer", "reservation"]

    created = (
        await db_session.execute(select(Reservation).where(Reservation.customer_id == raj.id))
    ).scalar_one_or_none()
    assert created is not None
    assert created.party_size == 2
