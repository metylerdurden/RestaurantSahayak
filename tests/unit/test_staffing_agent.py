"""StaffingAgent against a scripted LLMProvider and the real staffing Tool classes
wired to a mocked StaffingService — no real LLM, no database. Proves the agent is
correctly assembled (name="staffing", STAFFING_AGENT_SYSTEM_PROMPT, exactly the
three requested tools) and that it can carry a scripted tool-call sequence through
each of the required scenarios: normal staffing, understaffing, overstaffing, high
reservation volume, missing staff data, and recommendation generation. Real Qwen3-8B
reasoning is exercised in tests/integration/test_staffing_agent_live.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.prompts import STAFFING_AGENT_SYSTEM_PROMPT
from app.agents.staffing_agent import StaffingAgent
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.staffing_service import StaffingService
from app.tools.staffing_tools import (
    CalculateStaffRequirementTool,
    GetStaffAvailabilityTool,
    GetStaffScheduleTool,
)

RESTAURANT_ID = uuid.uuid4()
WINDOW_START = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
WINDOW_END = WINDOW_START + timedelta(hours=4)


def _staff(**overrides):
    defaults = dict(id=uuid.uuid4(), name="Alex", role="server", is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _shift(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        start_at=WINDOW_START,
        end_at=WINDOW_END,
        required_staff_count=2,
        is_published=True,
        status="scheduled",
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
def staffing_service():
    return AsyncMock(spec=StaffingService)


@pytest.fixture
def tools(staffing_service):
    return [
        GetStaffScheduleTool(staffing_service),
        GetStaffAvailabilityTool(staffing_service),
        CalculateStaffRequirementTool(staffing_service),
    ]


def _schedule_call(shifts_with_assignments):
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[
            ToolCall(
                id="c0",
                name="get_staff_schedule",
                arguments={"date_from": WINDOW_START.isoformat(), "date_to": WINDOW_END.isoformat()},
            )
        ],
    )


def _requirement_call():
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[
            ToolCall(
                id="c1",
                name="calculate_staff_requirement",
                arguments={"start_at": WINDOW_START.isoformat(), "end_at": WINDOW_END.isoformat()},
            )
        ],
    )


def _availability_call(role: str | None = None):
    args = {"start_at": WINDOW_START.isoformat(), "end_at": WINDOW_END.isoformat()}
    if role:
        args["role"] = role
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[ToolCall(id="c2", name="get_staff_availability", arguments=args)],
    )


def test_staffing_agent_is_named_and_prompted_correctly(agent_run_service, tools):
    agent = StaffingAgent(llm=ScriptedLLM([]), tools=tools, agent_run_service=agent_run_service)
    assert agent.name == "staffing"
    assert agent.system_prompt == STAFFING_AGENT_SYSTEM_PROMPT
    assert set(agent.tools) == {"get_staff_schedule", "get_staff_availability", "calculate_staff_requirement"}


@pytest.mark.asyncio
async def test_normal_staffing_is_reported_as_adequate(agent_run_service, tools, staffing_service):
    server1, server2 = _staff(name="Alex"), _staff(name="Sam")
    shift = _shift(required_staff_count=2)
    staffing_service.get_staff_schedule.return_value = [(shift, [(server1, None), (server2, None)])]
    staffing_service.calculate_staff_requirement.return_value = (30, 2, 2, 1, 5)

    responses = [
        _schedule_call(None),
        _requirement_call(),
        LLMResponse(
            content="Friday 6-10pm is adequately staffed: 2 servers scheduled, 2 required.", model="fake-model"
        ),
    ]
    agent = StaffingAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Is Friday dinner adequately staffed?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_staff_schedule", "calculate_staff_requirement"]
    assert "adequately staffed" in result.summary.lower()


@pytest.mark.asyncio
async def test_understaffing_triggers_availability_lookup_and_recommendation(
    agent_run_service, tools, staffing_service
):
    server1 = _staff(name="Alex", role="server")
    shift = _shift(required_staff_count=3)
    staffing_service.get_staff_schedule.return_value = [(shift, [(server1, None)])]
    staffing_service.calculate_staff_requirement.return_value = (60, 4, 3, 1, 8)
    free_staff = _staff(name="Priya", role="server")
    staffing_service.get_staff_availability.return_value = [free_staff]

    responses = [
        _schedule_call(None),
        _requirement_call(),
        _availability_call(role="server"),
        LLMResponse(
            content="Friday 6-10pm is understaffed: 1 server scheduled, 4 required. "
            "Request 3 more servers. Priya is available and could be added.",
            model="fake-model",
        ),
    ]
    agent = StaffingAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Is Friday dinner staffed correctly?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == [
        "get_staff_schedule",
        "calculate_staff_requirement",
        "get_staff_availability",
    ]
    assert "priya" in result.summary.lower()
    assert "3 more" in result.summary.lower() or "request 3" in result.summary.lower()
    staffing_service.get_staff_availability.assert_called_once()


@pytest.mark.asyncio
async def test_overstaffing_flags_excess_capacity_without_availability_lookup(
    agent_run_service, tools, staffing_service
):
    staff = [_staff(name=f"Staff{i}") for i in range(5)]
    shift = _shift(required_staff_count=2)
    staffing_service.get_staff_schedule.return_value = [(shift, [(s, None) for s in staff])]
    staffing_service.calculate_staff_requirement.return_value = (20, 2, 1, 1, 4)

    responses = [
        _schedule_call(None),
        _requirement_call(),
        LLMResponse(
            content="Friday 6-10pm has excess capacity: 5 staff scheduled, only 4 required.",
            model="fake-model",
        ),
    ]
    agent = StaffingAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Is Friday dinner overstaffed?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [tc.tool_name for tc in result.tool_calls] == ["get_staff_schedule", "calculate_staff_requirement"]
    assert "excess capacity" in result.summary.lower()
    staffing_service.get_staff_availability.assert_not_called()


@pytest.mark.asyncio
async def test_high_reservation_volume_scales_the_requirement(agent_run_service, tools, staffing_service):
    shift = _shift(required_staff_count=2)
    staffing_service.get_staff_schedule.return_value = [(shift, [(_staff(), None), (_staff(), None)])]
    # 300 covers -> well beyond a normal shift, required counts scale up sharply
    staffing_service.calculate_staff_requirement.return_value = (300, 20, 15, 1, 36)

    responses = [
        _schedule_call(None),
        _requirement_call(),
        _availability_call(),
        LLMResponse(
            content="Saturday night expects 300 covers, requiring 20 servers and 15 cooks — "
            "only 2 are scheduled. Request 18 more servers and 15 more cooks immediately.",
            model="fake-model",
        ),
    ]
    agent = StaffingAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle(
        "We have a huge reservation volume Saturday night, are we staffed for it?",
        restaurant_id=RESTAURANT_ID,
    )

    assert result.status == "completed"
    requirement_call = next(tc for tc in result.tool_calls if tc.tool_name == "calculate_staff_requirement")
    assert requirement_call.output["expected_covers"] == 300
    assert requirement_call.output["required_total"] == 36


@pytest.mark.asyncio
async def test_missing_staff_data_is_reported_plainly_not_fabricated(agent_run_service, tools, staffing_service):
    staffing_service.get_staff_schedule.return_value = []  # no shifts scheduled at all
    staffing_service.calculate_staff_requirement.return_value = (18, 2, 1, 1, 4)
    staffing_service.get_staff_availability.return_value = []  # no staff free either

    responses = [
        _schedule_call(None),
        _requirement_call(),
        _availability_call(),
        LLMResponse(
            content="No shifts are currently scheduled for that window, and no staff show as available. "
            "Based on expected covers, you should schedule 2 servers, 1 cook, and 1 host from scratch.",
            model="fake-model",
        ),
    ]
    agent = StaffingAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("What's the staffing situation next Tuesday?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    schedule_call = next(tc for tc in result.tool_calls if tc.tool_name == "get_staff_schedule")
    assert schedule_call.output["shifts"] == []
    assert "no shifts are currently scheduled" in result.summary.lower()
    assert "from scratch" in result.summary.lower()


@pytest.mark.asyncio
async def test_recommendation_is_grounded_in_the_tool_output(agent_run_service, tools, staffing_service):
    shift = _shift(required_staff_count=2)
    staffing_service.get_staff_schedule.return_value = [(shift, [(_staff(), None)])]
    staffing_service.calculate_staff_requirement.return_value = (45, 3, 3, 1, 7)
    staffing_service.get_staff_availability.return_value = [_staff(name="Priya"), _staff(name="Jo")]

    responses = [
        _schedule_call(None),
        _requirement_call(),
        _availability_call(),
        LLMResponse(content="Request 2 more servers for the shift; Priya and Jo are available.", model="fake-model"),
    ]
    agent = StaffingAgent(llm=ScriptedLLM(responses), tools=tools, agent_run_service=agent_run_service)

    result = await agent.handle("Check staffing for tomorrow's dinner shift.", restaurant_id=RESTAURANT_ID)

    # The final recommendation must be traceable to real tool output, not invented —
    # the last tool call's output is exactly what data the agent had to work with.
    assert result.data is not None
    assert result.data["available_staff"][0]["name"] in {"Priya", "Jo"}
    assert any(
        tc.tool_name == "calculate_staff_requirement" and tc.output["required_total"] == 7 for tc in result.tool_calls
    )
