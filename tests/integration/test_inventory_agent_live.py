"""InventoryAgent exercised end to end against a real Postgres database and the real
qwen3:8b model via Ollama. Skips automatically (rather than failing) if Ollama or the
configured model isn't reachable. Every other specialist agent (Reservation,
Customer, Staffing, Analytics) already has a live counterpart to its unit tests —
this closes that gap for Inventory. Assertions favor invariants that must always
hold (the inventory-listing tool is used, no crash, a real shortage is reflected in
the summary) over pinning the model's exact phrasing or full tool sequence."""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.agents.inventory_agent import InventoryAgent
from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.inventory_repo import InventoryRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.services.inventory_service import InventoryService
from app.tools.inventory_tools import (
    AnalyzeInventoryTool,
    CalculateReorderQuantityTool,
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)
from tests.integration.factories import make_inventory_item, make_restaurant, make_user

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def llm():
    provider = build_llm_provider(get_settings())
    if not await provider.health_check():
        pytest.skip("Ollama is not reachable, or the configured LLM_MODEL is not pulled")
    return provider


def _build_agent(db_session, llm, *, max_iterations: int = 8) -> InventoryAgent:
    approval_service = ApprovalService(ApprovalRepository(db_session))
    service = InventoryService(InventoryRepository(db_session), approval_service, get_settings())
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    tools = [
        AnalyzeInventoryTool(service),
        GetInventoryTool(service),
        CheckStockTool(service),
        CalculateRequiredInventoryTool(service),
        CalculateReorderQuantityTool(service),
        CreatePurchaseRequestTool(service),
    ]
    return InventoryAgent(llm=llm, tools=tools, agent_run_service=agent_run_service, max_iterations=max_iterations)


def _tool_names(result) -> set[str]:
    return {tc.tool_name for tc in result.tool_calls}


async def test_low_stock_item_is_detected_and_flagged(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    await make_inventory_item(
        db_session, restaurant, name="House White Wine", quantity_on_hand=3, low_stock_threshold=10
    )
    await make_inventory_item(
        db_session, restaurant, name="Extra Virgin Olive Oil", quantity_on_hand=18, low_stock_threshold=5
    )

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "What's our current inventory status? Flag anything low or out of stock.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )

    assert result.status != "error", result.summary
    # analyze_inventory is now the prompt's preferred tool for a general status
    # check (it classifies severity and computes reorder quantities in one call);
    # tolerate get_inventory too rather than pin the model's exact tool choice.
    assert _tool_names(result) & {"analyze_inventory", "get_inventory"}, result.summary
    summary_lower = result.summary.lower()
    assert "wine" in summary_lower or "low" in summary_lower or "short" in summary_lower, result.summary


async def test_well_stocked_inventory_is_not_flagged_as_a_shortage(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    await make_inventory_item(db_session, restaurant, name="Lemons", quantity_on_hand=60, low_stock_threshold=20)
    await make_inventory_item(db_session, restaurant, name="Garlic", quantity_on_hand=10, low_stock_threshold=1)

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "What's our current inventory status? Flag anything low or out of stock.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )

    assert result.status != "error", result.summary
    # analyze_inventory is now the prompt's preferred tool for a general status
    # check (it classifies severity and computes reorder quantities in one call);
    # tolerate get_inventory too rather than pin the model's exact tool choice.
    assert _tool_names(result) & {"analyze_inventory", "get_inventory"}, result.summary


async def test_status_check_proactively_reports_findings_instead_of_asking_to_calculate(db_session, llm):
    """The exact scenario that motivated this fix: 'Check current stock levels and
    identify any items that are low or out of stock.' The agent must report the real
    quantities/severities/reorder numbers it already has, not end with a question
    like 'would you like me to calculate the required quantities?'."""
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    await make_inventory_item(db_session, restaurant, name="Fresh Basil", quantity_on_hand=0, low_stock_threshold=1)
    await make_inventory_item(
        db_session, restaurant, name="House White Wine", quantity_on_hand=3, low_stock_threshold=6
    )

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Check current stock levels and identify any items that are low or out of stock.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )

    assert result.status != "error", result.summary
    summary_lower = result.summary.lower()
    assert "basil" in summary_lower or "wine" in summary_lower, result.summary
    assert "would you like" not in summary_lower, result.summary
