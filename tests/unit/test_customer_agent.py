"""CustomerAgent against a scripted LLMProvider and the real customer/memory Tool
classes wired to mocked services — no real LLM, no database. Proves the agent is
correctly assembled (name="customer", CUSTOMER_AGENT_SYSTEM_PROMPT, the tool set
reaches CustomerService/MemoryService through the real typed-tool contracts) and that
a scripted tool-call sequence — resolve customer, then search memory, then add or
update — flows through correctly. Real Qwen3-8B tool-selection behavior and real
persistence are exercised in tests/integration/test_customer_agent_live.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.customer_agent import CustomerAgent
from app.agents.prompts import CUSTOMER_AGENT_SYSTEM_PROMPT
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.customer_service import CustomerService
from app.services.memory_service import MemoryService
from app.tools.customer_tools import GetCustomerTool
from app.tools.memory_tools import AddMemoryTool, SearchMemoryTool, UpdateMemoryTool

RESTAURANT_ID = uuid.uuid4()


def _customer(**overrides):
    defaults = dict(id=uuid.uuid4(), name="Raj Patel", phone="+15551239999", email=None, is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _memory(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        customer_id=None,
        agent_name=None,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a window table."},
        importance=3,
        confidence=Decimal("1.00"),
        source="manager_stated",
        is_active=True,
        access_count=0,
        last_accessed_at=None,
        created_at=now,
        updated_at=now,
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
def customer_service():
    return AsyncMock(spec=CustomerService)


@pytest.fixture
def memory_service():
    return AsyncMock(spec=MemoryService)


@pytest.fixture
def tools(customer_service, memory_service):
    return [
        GetCustomerTool(customer_service),
        AddMemoryTool(memory_service),
        SearchMemoryTool(memory_service),
        UpdateMemoryTool(memory_service),
    ]


def test_customer_agent_is_named_and_prompted_correctly(agent_run_service, tools):
    agent = CustomerAgent(llm=ScriptedLLM([]), tools=tools, agent_run_service=agent_run_service)
    assert agent.name == "customer"
    assert agent.system_prompt == CUSTOMER_AGENT_SYSTEM_PROMPT
    assert {"get_customer", "add_memory", "search_memory", "update_memory"} <= set(agent.tools)


@pytest.mark.asyncio
async def test_customer_agent_resolves_customer_then_creates_memory(
    agent_run_service, tools, customer_service, memory_service
):
    raj = _customer()
    customer_service.get_customer.return_value = [raj]
    memory_service.add_memory.return_value = _memory(customer_id=raj.id)

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_customer", arguments={"query": "Raj"})],
        ),
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(
                id="c1", name="add_memory",
                arguments={
                    "memory_type": "CUSTOMER_PREFERENCE",
                    "topic": "seating_preference",
                    "content": {"text": "Prefers a quiet table away from the kitchen."},
                    "source": "manager_stated",
                    "customer_id": str(raj.id),
                },
            )],
        ),
        LLMResponse(content="Noted Raj's seating preference.", model="fake-model"),
    ]
    agent = CustomerAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle(
        "Raj prefers a quiet table away from the kitchen.", restaurant_id=RESTAURANT_ID
    )

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_customer", "add_memory"]
    memory_service.add_memory.assert_called_once()
    assert memory_service.add_memory.call_args.kwargs["customer_id"] == raj.id
    assert memory_service.add_memory.call_args.kwargs["memory_type"] == "CUSTOMER_PREFERENCE"


@pytest.mark.asyncio
async def test_customer_agent_searches_memory_and_reports_findings(
    agent_run_service, tools, customer_service, memory_service
):
    raj = _customer()
    customer_service.get_customer.return_value = [raj]
    found = _memory(customer_id=raj.id, content={"text": "Prefers a quiet table away from the kitchen."})
    memory_service.search_memory.return_value = [(found, 0.87)]

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_customer", arguments={"query": "Raj"})],
        ),
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(
                id="c1", name="search_memory",
                arguments={"query": "seating preference", "customer_id": str(raj.id)},
            )],
        ),
        LLMResponse(content="Raj prefers a quiet table away from the kitchen.", model="fake-model"),
    ]
    agent = CustomerAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Raj is coming Friday at 8. Anything I should know?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_customer", "search_memory"]
    assert result.tool_calls[-1].output["results"][0]["similarity"] == pytest.approx(0.87)
    assert "quiet table" in result.summary.lower()


@pytest.mark.asyncio
async def test_customer_agent_uses_update_memory_for_a_correction(
    agent_run_service, tools, customer_service, memory_service
):
    raj = _customer()
    customer_service.get_customer.return_value = [raj]
    existing = _memory(customer_id=raj.id, content={"text": "Prefers a table near the window."})
    memory_service.search_memory.return_value = [(existing, 0.9)]
    memory_service.update_memory.return_value = _memory(
        id=existing.id, customer_id=raj.id, content={"text": "Prefers a quiet table, not the window."}
    )

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_customer", arguments={"query": "Raj"})],
        ),
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(
                id="c1", name="search_memory",
                arguments={"query": "seating preference", "customer_id": str(raj.id)},
            )],
        ),
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(
                id="c2", name="update_memory",
                arguments={
                    "memory_id": str(existing.id),
                    "content": {"text": "Prefers a quiet table, not the window."},
                },
            )],
        ),
        LLMResponse(content="Updated Raj's seating preference.", model="fake-model"),
    ]
    agent = CustomerAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle(
        "Raj no longer wants a window table. He prefers a quiet table.", restaurant_id=RESTAURANT_ID
    )

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_customer", "search_memory", "update_memory"]
    memory_service.add_memory.assert_not_called()
    memory_service.update_memory.assert_called_once()
    assert memory_service.update_memory.call_args.kwargs["memory_id"] == existing.id
