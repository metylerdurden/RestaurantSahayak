"""InventoryAgent against a scripted LLMProvider and the real inventory Tool classes
wired to a mocked InventoryService — no real LLM, no database. Proves the agent is
correctly assembled and that a scripted low-stock -> reorder sequence, and the
pending-approval path for a large purchase request, flow through correctly. Real
Qwen3-8B reasoning is exercised as part of the orchestrator's live tests."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.inventory_agent import InventoryAgent
from app.agents.prompts import INVENTORY_AGENT_SYSTEM_PROMPT
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.inventory_service import InventoryService
from app.tools.base import PendingApprovalOutput
from app.tools.inventory_tools import (
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)

RESTAURANT_ID = uuid.uuid4()


def _item(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        name="Olive Oil",
        unit="liter",
        quantity_on_hand=Decimal("2"),
        low_stock_threshold=Decimal("10"),
        status="low",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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


@pytest.fixture
def agent_run_service():
    repo = AsyncMock(spec=AgentRunRepository)
    state: dict = {}

    async def _create(**kwargs):
        run = SimpleNamespace(id=uuid.uuid4(), outcome_summary=None, completed_at=None, **kwargs)
        state["run"] = run
        return run

    repo.create.side_effect = _create
    repo.next_sequence_number.return_value = 1
    repo.add_message.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)
    repo.get_by_id.side_effect = lambda run_id: state["run"]
    repo.save.side_effect = lambda run: run
    return AgentRunService(repo)


@pytest.fixture
def inventory_service():
    return AsyncMock(spec=InventoryService)


@pytest.fixture
def tools(inventory_service):
    return [
        GetInventoryTool(inventory_service),
        CheckStockTool(inventory_service),
        CalculateRequiredInventoryTool(inventory_service),
        CreatePurchaseRequestTool(inventory_service),
    ]


def test_inventory_agent_is_named_and_prompted_correctly(agent_run_service, tools):
    agent = InventoryAgent(llm=ScriptedLLM([]), tools=tools, agent_run_service=agent_run_service)
    assert agent.name == "inventory"
    assert agent.system_prompt == INVENTORY_AGENT_SYSTEM_PROMPT
    assert set(agent.tools) == {
        "get_inventory",
        "check_stock",
        "calculate_required_inventory",
        "create_purchase_request",
    }


@pytest.mark.asyncio
async def test_low_stock_lookup_and_reorder_recommendation(agent_run_service, tools, inventory_service):
    item = _item(name="Olive Oil", status="low", quantity_on_hand=Decimal("2"), low_stock_threshold=Decimal("10"))
    inventory_service.get_inventory.return_value = [item]
    inventory_service.calculate_required_inventory.return_value = (
        item,
        Decimal("1.5"),
        Decimal("-8.5"),
        Decimal("28.5"),
        14,
    )

    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_inventory", arguments={"status": "low"})],
        ),
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="c1", name="calculate_required_inventory", arguments={"item_id": str(item.id)})],
        ),
        LLMResponse(
            content="Olive Oil is low (2 liters on hand). Recommend ordering about 28.5 liters.", model="fake-model"
        ),
    ]
    agent = InventoryAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("What's low on stock and how much should we reorder?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_inventory", "calculate_required_inventory"]
    assert "28.5" in result.summary


@pytest.mark.asyncio
async def test_purchase_request_above_threshold_is_reported_as_pending_approval(
    agent_run_service, tools, inventory_service
):
    item = _item()
    inventory_service.get_inventory.return_value = [item]
    approval_id = uuid.uuid4()
    inventory_service.create_purchase_request.return_value = PendingApprovalOutput(
        approval_id=approval_id, summary="Purchase request exceeds the approval threshold."
    )

    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_inventory", arguments={"name_contains": "Olive Oil"})],
        ),
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="create_purchase_request",
                    arguments={"item_id": str(item.id), "requested_quantity": "50", "estimated_cost": "500"},
                )
            ],
        ),
        LLMResponse(content="This purchase request needs manager approval before it's placed.", model="fake-model"),
    ]
    agent = InventoryAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Order 50 liters of olive oil.", restaurant_id=RESTAURANT_ID)

    assert result.status == "pending_approval"
    assert result.data["approval_id"] == str(approval_id)
    assert "approval" in result.summary.lower()


@pytest.mark.asyncio
async def test_out_of_stock_item_reported_plainly(agent_run_service, tools, inventory_service):
    inventory_service.get_inventory.return_value = []

    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_inventory", arguments={"status": "out_of_stock"})],
        ),
        LLMResponse(content="Nothing is currently marked out of stock.", model="fake-model"),
    ]
    agent = InventoryAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Is anything out of stock?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert result.tool_calls[0].output["items"] == []
    assert "nothing" in result.summary.lower() or "out of stock" in result.summary.lower()
