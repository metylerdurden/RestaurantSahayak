"""AnalyticsAgent against a scripted LLMProvider and the real analytics Tool classes
wired to a mocked AnalyticsService — no real LLM, no database. Proves the agent is
correctly assembled (name="analytics", ANALYTICS_AGENT_SYSTEM_PROMPT, exactly the
three requested tools) and that scripted multi-call comparisons flow through
correctly, including the "no data" path. Real Qwen3-8B reasoning against real data is
exercised in tests/integration/test_analytics_agent.py (real DB, scripted LLM) and
tests/integration/test_analytics_agent_live.py (real DB, real model)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.analytics_agent import AnalyticsAgent
from app.agents.prompts import ANALYTICS_AGENT_SYSTEM_PROMPT
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.schemas.analytics import DailySalesEntry, ItemSalesEntry
from app.services.agent_run_service import AgentRunService
from app.services.analytics_service import AnalyticsService
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool

RESTAURANT_ID = uuid.uuid4()


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
def analytics_service():
    return AsyncMock(spec=AnalyticsService)


@pytest.fixture
def tools(analytics_service):
    return [
        GetDailySalesTool(analytics_service),
        GetItemSalesTool(analytics_service),
        GetNoShowRateTool(analytics_service),
    ]


def _daily_sales_call(call_id: str, date_from: str, date_to: str):
    return LLMResponse(
        content="", model="fake-model",
        tool_calls=[ToolCall(id=call_id, name="get_daily_sales", arguments={"date_from": date_from, "date_to": date_to})],
    )


def test_analytics_agent_is_named_and_prompted_correctly(agent_run_service, tools):
    agent = AnalyticsAgent(llm=ScriptedLLM([]), tools=tools, agent_run_service=agent_run_service)
    assert agent.name == "analytics"
    assert agent.system_prompt == ANALYTICS_AGENT_SYSTEM_PROMPT
    assert set(agent.tools) == {"get_daily_sales", "get_item_sales", "get_no_show_rate"}


@pytest.mark.asyncio
async def test_revenue_drop_question_compares_two_days(agent_run_service, tools, analytics_service):
    analytics_service.get_daily_sales.side_effect = [
        [DailySalesEntry(sales_date=date(2026, 8, 18), revenue=Decimal("412.00"), items_sold=30, covers=22)],
        [DailySalesEntry(sales_date=date(2026, 8, 17), revenue=Decimal("610.00"), items_sold=45, covers=34)],
    ]

    responses = [
        _daily_sales_call("c0", "2026-08-18", "2026-08-18"),
        _daily_sales_call("c1", "2026-08-17", "2026-08-17"),
        LLMResponse(
            content="Revenue on Aug 18 was $412.00, down from $610.00 on Aug 17 — a drop of about 32%. "
            "This may be because covers were also lower (22 vs 34); the tools don't show a root cause.",
            model="fake-model",
        ),
    ]
    agent = AnalyticsAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Why was revenue lower yesterday?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_daily_sales", "get_daily_sales"]
    assert result.tool_calls[0].output["days"][0]["revenue"] == "412.00" or float(
        result.tool_calls[0].output["days"][0]["revenue"]
    ) == 412.0
    assert "412" in result.summary and "610" in result.summary
    assert "may be" in result.summary.lower()  # hedged, not stated as fact


@pytest.mark.asyncio
async def test_top_item_question_uses_item_sales(agent_run_service, tools, analytics_service):
    analytics_service.get_item_sales.return_value = [
        ItemSalesEntry(menu_item_id=uuid.uuid4(), name="Lamb Souvlaki", quantity_sold=58, revenue=Decimal("1508.00")),
        ItemSalesEntry(menu_item_id=uuid.uuid4(), name="Baklava", quantity_sold=40, revenue=Decimal("320.00")),
    ]

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(
                id="c0", name="get_item_sales",
                arguments={"date_from": "2026-08-12", "date_to": "2026-08-18", "limit": 5},
            )],
        ),
        LLMResponse(content="Lamb Souvlaki performed best this week with 58 sold ($1508.00), ahead of Baklava at 40.", model="fake-model"),
    ]
    agent = AnalyticsAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Which menu items performed best this week?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_item_sales"]
    assert result.data["items"][0]["name"] == "Lamb Souvlaki"
    assert "lamb souvlaki" in result.summary.lower()


@pytest.mark.asyncio
async def test_no_show_trend_question_compares_two_periods(agent_run_service, tools, analytics_service):
    analytics_service.get_no_show_rate.side_effect = [
        (8, 42, Decimal("0.1600")),
        (3, 47, Decimal("0.0600")),
    ]

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_no_show_rate", arguments={"date_from": "2026-08-11", "date_to": "2026-08-17"})],
        ),
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c1", name="get_no_show_rate", arguments={"date_from": "2026-08-04", "date_to": "2026-08-10"})],
        ),
        LLMResponse(
            content="No-shows this week are 16% (8 of 50), up from 6% (3 of 50) last week — an increase.",
            model="fake-model",
        ),
    ]
    agent = AnalyticsAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Are our no-shows increasing?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_no_show_rate", "get_no_show_rate"]
    assert result.tool_calls[0].output["no_show_count"] == 8
    assert result.tool_calls[1].output["no_show_count"] == 3
    assert "increase" in result.summary.lower() or "up from" in result.summary.lower()


@pytest.mark.asyncio
async def test_weekend_comparison_uses_two_daily_sales_ranges(agent_run_service, tools, analytics_service):
    analytics_service.get_daily_sales.side_effect = [
        [
            DailySalesEntry(sales_date=date(2026, 8, 15), revenue=Decimal("900.00"), items_sold=60, covers=50),
            DailySalesEntry(sales_date=date(2026, 8, 16), revenue=Decimal("950.00"), items_sold=65, covers=55),
        ],
        [
            DailySalesEntry(sales_date=date(2026, 8, 8), revenue=Decimal("700.00"), items_sold=50, covers=40),
            DailySalesEntry(sales_date=date(2026, 8, 9), revenue=Decimal("720.00"), items_sold=52, covers=42),
        ],
    ]

    responses = [
        _daily_sales_call("c0", "2026-08-15", "2026-08-16"),
        _daily_sales_call("c1", "2026-08-08", "2026-08-09"),
        LLMResponse(
            content="This weekend brought in $1850.00 total, up from $1420.00 last weekend.",
            model="fake-model",
        ),
    ]
    agent = AnalyticsAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("How did this weekend compare with last weekend?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_daily_sales", "get_daily_sales"]
    assert len(result.tool_calls[0].output["days"]) == 2
    assert len(result.tool_calls[1].output["days"]) == 2


@pytest.mark.asyncio
async def test_insufficient_data_is_reported_not_fabricated(agent_run_service, tools, analytics_service):
    analytics_service.get_no_show_rate.return_value = (0, 0, None)

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_no_show_rate", arguments={"date_from": "2026-01-01", "date_to": "2026-01-07"})],
        ),
        LLMResponse(
            content="There isn't enough completed-reservation data in that period to compute a no-show rate.",
            model="fake-model",
        ),
    ]
    agent = AnalyticsAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("What was our no-show rate in early January?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert result.tool_calls[0].output["no_show_rate"] is None
    assert "enough" in result.summary.lower() or "no data" in result.summary.lower() or "isn't enough" in result.summary.lower()
    # no fabricated percentage sign backed by nothing
    assert "%" not in result.summary
