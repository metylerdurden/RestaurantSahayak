"""ReservationAgent loop mechanics against a scripted LLMProvider and small fake
tools — no real LLM, no database. Proves: tool selection/execution, multi-step
looping, safe handling of tool errors and hallucinated tool names, the iteration
cap, pending-approval propagation, and top-level failure containment. Real tool
integration (the actual reservation tools) is exercised end to end against a live
Ollama model in tests/integration/test_reservation_agent_live.py."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.agents.reservation_agent import ReservationAgent
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.tools.base import PendingApprovalOutput, Tool, ToolContext, ToolError

# --- fake tools (isolated from the real reservation tools on purpose) ---


class EchoInput(BaseModel):
    value: str


class EchoOutput(BaseModel):
    echoed: str


class EchoTool(Tool[EchoInput, EchoOutput]):
    name = "echo_tool"
    description = "Echoes back the given value."
    input_model = EchoInput
    output_model = EchoOutput

    async def run(self, input: EchoInput, *, context: ToolContext) -> EchoOutput:
        return EchoOutput(echoed=input.value)


class FailingInput(BaseModel):
    reason: str


class FailingOutput(BaseModel):
    ok: bool


class FailingTool(Tool[FailingInput, FailingOutput]):
    name = "failing_tool"
    description = "Always fails, for testing error handling."
    input_model = FailingInput
    output_model = FailingOutput

    async def run(self, input: FailingInput, *, context: ToolContext) -> FailingOutput:
        raise ToolError("boom", f"failed: {input.reason}")


class ApprovalInput(BaseModel):
    x: int


class ApprovalOutput(BaseModel):
    ok: bool


class ApprovalTool(Tool[ApprovalInput, ApprovalOutput]):
    name = "approval_tool"
    description = "Always proposes an approval, for testing pending_approval propagation."
    input_model = ApprovalInput
    output_model = ApprovalOutput
    high_impact = True

    def __init__(self, approval_id: uuid.UUID) -> None:
        self.approval_id = approval_id

    async def run(self, input: ApprovalInput, *, context: ToolContext) -> PendingApprovalOutput:
        return PendingApprovalOutput(approval_id=self.approval_id, summary="Needs manager approval")


# --- scripted LLM provider ---


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


class BoomLLM(LLMProvider):
    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
        raise RuntimeError("connection reset")

    async def health_check(self) -> bool:
        return False


# --- AgentRunService wired to a mocked repository (no database) ---


@pytest.fixture
def agent_run_repo():
    repo = AsyncMock(spec=AgentRunRepository)
    state: dict[str, SimpleNamespace] = {}

    async def _create(**kwargs):
        run = SimpleNamespace(id=uuid.uuid4(), outcome_summary=None, completed_at=None, **kwargs)
        state["run"] = run
        return run

    repo.create.side_effect = _create
    repo.next_sequence_number.return_value = 1
    repo.add_message.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)
    repo.get_by_id.side_effect = lambda run_id: state["run"]
    repo.save.side_effect = lambda run: run
    return repo


@pytest.fixture
def agent_run_service(agent_run_repo):
    return AgentRunService(agent_run_repo)


# --- tests ---


@pytest.mark.asyncio
async def test_agent_executes_tool_then_returns_completed_result(agent_run_service):
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="call_0", name="echo_tool", arguments={"value": "hi"})],
        ),
        LLMResponse(content="Done: echoed hi", model="fake-model"),
    ]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[EchoTool()], agent_run_service=agent_run_service)

    result = await agent.handle("echo hi", restaurant_id=uuid.uuid4())

    assert result.status == "completed"
    assert result.summary == "Done: echoed hi"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "echo_tool"
    assert result.data == {"echoed": "hi"}
    assert result.error is None
    assert len(llm.calls) == 2
    # the tool result must have been fed back into the second call's conversation
    assert any(m.role == "tool" and "hi" in m.content for m in llm.calls[1])


@pytest.mark.asyncio
async def test_agent_performs_multiple_tool_calls_across_iterations(agent_run_service):
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="call_0", name="echo_tool", arguments={"value": "step1"})],
        ),
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="call_0", name="echo_tool", arguments={"value": "step2"})],
        ),
        LLMResponse(content="Finished both steps.", model="fake-model"),
    ]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[EchoTool()], agent_run_service=agent_run_service)

    result = await agent.handle("do two steps", restaurant_id=uuid.uuid4())

    assert result.status == "completed"
    assert [tc.output["echoed"] for tc in result.tool_calls] == ["step1", "step2"]
    assert len(llm.calls) == 3


@pytest.mark.asyncio
async def test_agent_handles_tool_error_and_recovers_with_final_message(agent_run_service):
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="call_0", name="failing_tool", arguments={"reason": "bad input"})],
        ),
        LLMResponse(content="I could not complete that: bad input", model="fake-model"),
    ]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[FailingTool()], agent_run_service=agent_run_service)

    result = await agent.handle("try something", restaurant_id=uuid.uuid4())

    assert result.status == "completed"
    assert result.tool_calls == []  # a failed call is not recorded as a successful tool call
    assert "could not complete" in result.summary.lower()
    assert any(m.role == "tool" and "boom" in m.content for m in llm.calls[1])


@pytest.mark.asyncio
async def test_agent_handles_hallucinated_tool_name_safely(agent_run_service):
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="call_0", name="not_a_real_tool", arguments={})],
        ),
        LLMResponse(content="I don't have a way to do that.", model="fake-model"),
    ]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[EchoTool()], agent_run_service=agent_run_service)

    result = await agent.handle("do something weird", restaurant_id=uuid.uuid4())

    assert result.status == "completed"
    assert result.tool_calls == []
    assert any(m.role == "tool" and "unknown_tool" in m.content for m in llm.calls[1])


@pytest.mark.asyncio
async def test_agent_bails_out_after_max_iterations(agent_run_service):
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id=f"call_{i}", name="echo_tool", arguments={"value": "x"})],
        )
        for i in range(10)
    ]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[EchoTool()], agent_run_service=agent_run_service, max_iterations=3)

    result = await agent.handle("loop forever", restaurant_id=uuid.uuid4())

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "max_iterations_exceeded"
    assert len(result.tool_calls) == 3
    assert len(llm.calls) == 3


@pytest.mark.asyncio
async def test_agent_reports_pending_approval_status(agent_run_service):
    approval_id = uuid.uuid4()
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="call_0", name="approval_tool", arguments={"x": 1})],
        ),
        LLMResponse(content="This needs manager approval before it happens.", model="fake-model"),
    ]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[ApprovalTool(approval_id)], agent_run_service=agent_run_service)

    result = await agent.handle("cancel the big party", restaurant_id=uuid.uuid4())

    assert result.status == "pending_approval"
    assert result.data["approval_id"] == str(approval_id)


@pytest.mark.asyncio
async def test_agent_returns_error_result_on_unexpected_llm_failure(agent_run_service, agent_run_repo):
    agent = ReservationAgent(llm=BoomLLM(), tools=[EchoTool()], agent_run_service=agent_run_service)

    result = await agent.handle("do something", restaurant_id=uuid.uuid4())

    assert result.status == "error"
    assert result.error.code == "internal_error"
    saved_run = agent_run_repo.save.call_args.args[0]
    assert saved_run.status == "failed"


@pytest.mark.asyncio
async def test_agent_records_model_name_on_run(agent_run_service, agent_run_repo):
    responses = [LLMResponse(content="Done.", model="fake-model")]
    llm = ScriptedLLM(responses)
    agent = ReservationAgent(llm=llm, tools=[EchoTool()], agent_run_service=agent_run_service)

    await agent.handle("do nothing", restaurant_id=uuid.uuid4())

    assert agent_run_repo.create.call_args.kwargs["model_name"] == "fake-model"
    assert agent_run_repo.create.call_args.kwargs["agent_name"] == "reservation"
