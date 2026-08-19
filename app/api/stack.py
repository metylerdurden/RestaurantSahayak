"""Assembles the full DineOps agent/service stack from a single AsyncSession — the
one place real application code (the manual-trigger workflow routes,
scripts/run_workflow.py) builds every repository, service, tool, agent, and
background workflow. Test suites deliberately build their own smaller stacks (to
isolate exactly what each test exercises); this is the "real" wiring used when the
app is actually running.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics_agent import AnalyticsAgent
from app.agents.customer_agent import CustomerAgent
from app.agents.inventory_agent import InventoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.reservation_agent import ReservationAgent
from app.agents.staffing_agent import StaffingAgent
from app.core.config import get_settings
from app.embeddings.factory import get_embedding_provider
from app.llm.factory import get_llm_provider
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.analytics_repo import AnalyticsRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.event_repo import EventRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.memory_repo import MemoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.repositories.staffing_repo import StaffingRepository
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.analytics_service import AnalyticsService
from app.services.approval_execution import build_executors
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.event_bus import InProcessEventBus
from app.services.inventory_service import InventoryService
from app.services.memory_service import MemoryService
from app.services.reservation_service import ReservationService
from app.services.staffing_service import StaffingService
from app.services.workflow_run_service import WorkflowRunService
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool
from app.tools.customer_tools import GetCustomerHistoryTool, GetCustomerTool, UpdateCustomerTool
from app.tools.inventory_tools import (
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)
from app.tools.memory_tools import (
    AddMemoryTool,
    DeleteMemoryTool,
    ForgetMemoryTool,
    GetMemoryTool,
    ReinforceMemoryTool,
    SearchMemoryTool,
    UpdateMemoryTool,
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
from app.workflows import build_background_workflows, register_default_workflows
from app.workflows.background_workflow import BackgroundWorkflow


@dataclass
class AgentStack:
    orchestrator: OrchestratorAgent
    reservation_agent: ReservationAgent
    customer_agent: CustomerAgent
    inventory_agent: InventoryAgent
    staffing_agent: StaffingAgent
    analytics_agent: AnalyticsAgent
    workflow_run_service: WorkflowRunService
    background_workflows: dict[str, BackgroundWorkflow]


def build_agent_stack(session: AsyncSession) -> AgentStack:
    settings = get_settings()
    llm = get_llm_provider()
    embeddings = get_embedding_provider()

    agent_run_service = AgentRunService(AgentRunRepository(session))
    memory_service = MemoryService(MemoryRepository(session), embeddings)
    event_bus = InProcessEventBus(EventRepository(session))

    approval_repo = ApprovalRepository(session)
    reservation_repo = ReservationRepository(session)
    customer_repo = CustomerRepository(session)
    inventory_repo = InventoryRepository(session)

    # ApprovalService needs the domain services' executors, which need a
    # ApprovalService reference themselves — build a plain one first, the domain
    # services against it, then the full (execute + remember) ApprovalService, and
    # repoint the domain services at it. Same two-phase wiring as
    # tests/integration/test_approval_workflow.py.
    approval_service = ApprovalService(approval_repo)
    reservation_service = ReservationService(
        reservation_repo, customer_repo, approval_service, settings, event_bus=event_bus
    )
    inventory_service = InventoryService(inventory_repo, approval_service, settings, event_bus=event_bus)
    full_approval_service = ApprovalService(
        approval_repo,
        executors=build_executors(reservation_service=reservation_service, inventory_service=inventory_service),
        memory_service=memory_service,
        event_bus=event_bus,
    )
    reservation_service.approval_service = full_approval_service
    inventory_service.approval_service = full_approval_service

    customer_service = CustomerService(customer_repo, event_bus=event_bus)
    staffing_service = StaffingService(StaffingRepository(session), settings)
    analytics_service = AnalyticsService(AnalyticsRepository(session))

    reservation_agent = ReservationAgent(
        llm=llm,
        tools=[
            GetReservationsTool(reservation_service),
            FindAvailableTableTool(reservation_service),
            CreateReservationTool(reservation_service),
            ModifyReservationTool(reservation_service),
            CancelReservationTool(reservation_service),
        ],
        agent_run_service=agent_run_service,
    )
    customer_agent = CustomerAgent(
        llm=llm,
        tools=[
            GetCustomerTool(customer_service),
            GetCustomerHistoryTool(customer_service),
            UpdateCustomerTool(customer_service),
            AddMemoryTool(memory_service),
            SearchMemoryTool(memory_service),
            GetMemoryTool(memory_service),
            UpdateMemoryTool(memory_service),
            ReinforceMemoryTool(memory_service),
            ForgetMemoryTool(memory_service),
            DeleteMemoryTool(memory_service),
        ],
        agent_run_service=agent_run_service,
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
    staffing_agent = StaffingAgent(
        llm=llm,
        tools=[
            GetStaffScheduleTool(staffing_service),
            GetStaffAvailabilityTool(staffing_service),
            CalculateStaffRequirementTool(staffing_service),
        ],
        agent_run_service=agent_run_service,
    )
    analytics_agent = AnalyticsAgent(
        llm=llm,
        tools=[
            GetDailySalesTool(analytics_service),
            GetItemSalesTool(analytics_service),
            GetNoShowRateTool(analytics_service),
        ],
        agent_run_service=agent_run_service,
    )

    orchestrator = OrchestratorAgent(
        llm=llm,
        specialists={
            "reservation": reservation_agent,
            "customer": customer_agent,
            "inventory": inventory_agent,
            "staffing": staffing_agent,
            "analytics": analytics_agent,
        },
        agent_run_service=agent_run_service,
        approval_service=full_approval_service,
    )

    # Reactive event workflows (Step 16) — e.g. reservation.created checked against
    # inventory — fire for real whenever these services publish.
    register_default_workflows(event_bus, inventory_agent=inventory_agent)

    workflow_run_repo = WorkflowRunRepository(session)
    background_workflows = build_background_workflows(
        workflow_run_repo=workflow_run_repo,
        orchestrator=orchestrator,
        inventory_agent=inventory_agent,
        staffing_agent=staffing_agent,
    )
    workflow_run_service = WorkflowRunService(workflow_run_repo, AgentRunRepository(session))

    return AgentStack(
        orchestrator=orchestrator,
        reservation_agent=reservation_agent,
        customer_agent=customer_agent,
        inventory_agent=inventory_agent,
        staffing_agent=staffing_agent,
        analytics_agent=analytics_agent,
        workflow_run_service=workflow_run_service,
        background_workflows=background_workflows,
    )
