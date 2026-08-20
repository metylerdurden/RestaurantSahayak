"""Step 20 reliability hardening for the shared ToolCallingAgent loop — no real LLM,
no database. Proves two things every concrete specialist agent inherits for free:

1. An unexpected (non-ToolError, non-ValidationError) exception from inside a tool
   is surfaced as a structured error fed back to the model, instead of aborting
   the whole run on a single possibly-transient failure — the agent gets a chance
   to recover, and the model never sees a fabricated result, only an honest error.
2. handle()'s own documented contract — "always returns, never raises" — holds
   even when persisting the final AgentRun status itself fails (e.g. a database
   outage right at the end of an otherwise-successful run).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.agents.tool_calling_agent import ToolCallingAgent
from app.llm.base import LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.tools.base import Tool, ToolContext

RESTAURANT_ID = uuid.uuid4()


class _EmptyInput(BaseModel):
    pass


class _EmptyOutput(BaseModel):
    ok: bool = True


class FlakyTool(Tool[_EmptyInput, _EmptyOutput]):
    name = "flaky_tool"
    description = "A tool that always raises an unexpected (non-ToolError) exception."
    input_model = _EmptyInput
    output_model = _EmptyOutput

    async def run(self, input: _EmptyInput, *, context: ToolContext) -> _EmptyOutput:
        raise RuntimeError("simulated unexpected failure below the typed-tool boundary")


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
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


@pytest.mark.asyncio
async def test_unexpected_tool_exception_becomes_a_structured_error_and_the_run_continues(agent_run_service):
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="c0", name="flaky_tool", arguments={})],
        ),
        LLMResponse(content="I couldn't complete that due to a tool error.", model="fake-model"),
    ]
    agent = ToolCallingAgent(
        name="test_agent",
        system_prompt="You are a test agent.",
        llm=ScriptedLLM(responses),
        tools=[FlakyTool()],
        agent_run_service=agent_run_service,
    )

    result = await agent.handle("Do the flaky thing", restaurant_id=RESTAURANT_ID)

    # The run continued past the tool failure and reached a normal final answer —
    # it did not abort to status="error" on the first unexpected exception.
    assert result.status == "completed"
    assert "tool error" in result.summary.lower()


@pytest.mark.asyncio
async def test_handle_never_raises_even_if_recording_completion_fails(agent_run_service):
    agent_run_service.complete_run = AsyncMock(side_effect=RuntimeError("database unreachable"))
    responses = [LLMResponse(content="All done.", model="fake-model")]
    agent = ToolCallingAgent(
        name="test_agent",
        system_prompt="You are a test agent.",
        llm=ScriptedLLM(responses),
        tools=[],
        agent_run_service=agent_run_service,
    )

    result = await agent.handle("Just answer", restaurant_id=RESTAURANT_ID)  # must not raise

    assert result.status == "completed"
    assert result.summary == "All done."
