"""OrchestratorAgent against a scripted LLMProvider and fake specialist agents — no
real LLM, no database, no real specialist logic. Proves the LangGraph wiring itself:
delegation loop, multi-agent sequencing, state passed to each specialist
(parent_run_id/correlation_id linking), retry-on-error, the max-delegations guard,
pending-approval surfacing, and graceful handling of malformed/unrecognized routing
decisions. Real Qwen3-8B routing/combination behavior against real specialist agents
is exercised in tests/integration/test_orchestrator_agent.py (real DB, scripted LLMs)
and tests/integration/test_orchestrator_agent_live.py (real model)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.state import AgentResult, ToolCallRecord
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService

RESTAURANT_ID = uuid.uuid4()


class FakeSpecialist:
    """A stand-in for a real ToolCallingAgent subclass — records exactly what it was
    called with and returns pre-scripted AgentResults, one per call."""

    def __init__(self, name: str, results: list[AgentResult]) -> None:
        self.name = name
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def handle(self, task: str, **kwargs) -> AgentResult:
        self.calls.append((task, kwargs))
        return self._results.pop(0)


def _result(status="completed", summary="ok", data=None, tool_calls=None) -> AgentResult:
    return AgentResult(
        agent_run_id=uuid.uuid4(),
        status=status,
        summary=summary,
        tool_calls=tool_calls or [],
        data=data,
        error=None,
        latency_ms=5,
    )


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[LLMMessage], list | None]] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
        self.calls.append((list(messages), tools))
        return self._responses.pop(0)

    async def health_check(self) -> bool:
        return True


def _delegate(call_id: str, agent_name: str, instruction: str) -> LLMResponse:
    return LLMResponse(
        content="", model="fake-model",
        tool_calls=[ToolCall(id=call_id, name="delegate", arguments={"agent_name": agent_name, "instruction": instruction})],
    )


def _finish(call_id: str = "finish") -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name="finish", arguments={})])


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake-model")


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
async def test_single_specialist_delegation_and_combine(agent_run_service):
    reservation = FakeSpecialist("reservation", [_result(summary="3 reservations tonight, 12 covers.")])
    responses = [
        _delegate("c0", "reservation", "How many reservations are there tonight?"),
        _finish(),
        _final("We have 3 reservations tonight totaling 12 covers."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"reservation": reservation}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert result.summary == "We have 3 reservations tonight totaling 12 covers."
    assert [inv.agent_name for inv in result.invocations] == ["reservation"]
    assert reservation.calls[0][0] == "How many reservations are there tonight?"
    assert reservation.calls[0][1]["restaurant_id"] == RESTAURANT_ID


@pytest.mark.asyncio
async def test_multi_agent_readiness_check_invokes_several_specialists_in_sequence(agent_run_service):
    reservation = FakeSpecialist("reservation", [_result(summary="12 covers booked tonight across 3 reservations.")])
    inventory = FakeSpecialist("inventory", [_result(summary="All key items are in stock.")])
    staffing = FakeSpecialist("staffing", [_result(summary="Tonight's shift is adequately staffed.")])
    analytics = FakeSpecialist("analytics", [_result(summary="Similar nights typically run smoothly.")])

    responses = [
        _delegate("c0", "reservation", "What reservations do we have tonight?"),
        _delegate("c1", "inventory", "Do we have enough stock for tonight's expected covers?"),
        _delegate("c2", "staffing", "Are we staffed for tonight's dinner shift?"),
        _delegate("c3", "analytics", "How have similar-volume nights gone recently?"),
        _finish(),
        _final(
            "Tonight looks good: 12 covers booked across 3 reservations, key inventory is in stock, "
            "and the shift is adequately staffed. Similar nights have gone smoothly."
        ),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={
            "reservation": reservation, "inventory": inventory, "staffing": staffing, "analytics": analytics,
        },
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [inv.agent_name for inv in result.invocations] == ["reservation", "inventory", "staffing", "analytics"]
    # Each specialist's contribution made it into the final combined response.
    assert "12 covers" in result.summary
    assert "stock" in result.summary
    assert "staffed" in result.summary
    for specialist in (reservation, inventory, staffing, analytics):
        assert len(specialist.calls) == 1


@pytest.mark.asyncio
async def test_customer_result_is_used_to_inform_the_reservation_instruction(agent_run_service):
    """Mirrors "Book Raj for Friday at 8": the customer agent's result (including a
    recalled preference) must be visible to the orchestrator when it writes the
    instruction for the reservation agent — proven here by scripting the second
    delegate() call to reference facts only available in the customer result."""
    customer = FakeSpecialist(
        "customer",
        [_result(summary="Raj Patel (id known). He prefers a quiet table away from the kitchen.")],
    )
    reservation = FakeSpecialist("reservation", [_result(summary="Booked Raj a quiet table for Friday 8pm.")])

    responses = [
        _delegate("c0", "customer", "Look up Raj and any known preferences."),
        _delegate(
            "c1", "reservation",
            "Book a table for Raj Patel for Friday at 8pm. He prefers a quiet table away from the kitchen.",
        ),
        _finish(),
        _final("Booked Raj a quiet table, away from the kitchen, for Friday at 8pm."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"customer": customer, "reservation": reservation},
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle("Book Raj for Friday at 8.", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert [inv.agent_name for inv in result.invocations] == ["customer", "reservation"]
    reservation_task = reservation.calls[0][0]
    assert "quiet table" in reservation_task  # the preference actually flowed into the delegated task
    assert "away from the kitchen" in result.summary


@pytest.mark.asyncio
async def test_pending_approval_result_is_surfaced_not_hidden(agent_run_service):
    approval_id = uuid.uuid4()
    reservation = FakeSpecialist(
        "reservation",
        [_result(status="pending_approval", summary="Cancelling this large party requires manager approval.", data={"approval_id": str(approval_id), "status": "pending_approval"})],
    )
    responses = [
        _delegate("c0", "reservation", "Cancel the reservation for the party of 8."),
        _finish(),
        _final("That cancellation requires manager approval and has not taken effect yet."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"reservation": reservation}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Cancel the party of 8's reservation.", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert len(result.pending_approvals) == 1
    assert result.pending_approvals[0].agent_name == "reservation"
    assert "approval" in result.summary.lower()


@pytest.mark.asyncio
async def test_failed_specialist_is_retried_once_then_accepted(agent_run_service):
    inventory = FakeSpecialist(
        "inventory",
        [_result(status="error", summary="internal error"), _result(status="completed", summary="Stock levels look fine.")],
    )
    responses = [
        _delegate("c0", "inventory", "Check stock levels."),
        _finish(),
        _final("Stock levels look fine."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"inventory": inventory}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Check our stock.", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert len(inventory.calls) == 2  # one retry after the first failure
    assert result.invocations[0].status == "completed"


@pytest.mark.asyncio
async def test_specialist_still_failing_after_retry_is_reported_not_hidden(agent_run_service):
    inventory = FakeSpecialist(
        "inventory",
        [_result(status="error", summary="internal error"), _result(status="error", summary="internal error")],
    )
    responses = [
        _delegate("c0", "inventory", "Check stock levels."),
        _finish(),
        _final("I couldn't check inventory due to a repeated error."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"inventory": inventory}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Check our stock.", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert len(inventory.calls) == 2
    assert result.invocations[0].status == "error"


@pytest.mark.asyncio
async def test_max_delegations_guard_forces_combine_eventually(agent_run_service):
    reservation = FakeSpecialist("reservation", [_result(summary=f"call {i}") for i in range(10)])
    # The model keeps wanting to delegate again, forever — the guard must still stop it.
    responses = [_delegate(f"c{i}", "reservation", f"task {i}") for i in range(10)]
    responses.append(_final("Reached the delegation limit; here's what I found."))
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"reservation": reservation},
        agent_run_service=agent_run_service,
        max_delegations=3,
    )

    result = await orchestrator.handle("Keep checking reservations.", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert len(result.invocations) == 3
    assert len(reservation.calls) == 3


@pytest.mark.asyncio
async def test_unknown_agent_name_in_routing_decision_is_handled_safely(agent_run_service):
    reservation = FakeSpecialist("reservation", [])
    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="delegate", arguments={"agent_name": "not_a_real_agent", "instruction": "x"})],
        ),
        _final("I don't have a specialist for that."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"reservation": reservation}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Do something unsupported.", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    assert result.invocations == []
    assert reservation.calls == []


@pytest.mark.asyncio
async def test_orchestrator_returns_error_result_on_unexpected_failure(agent_run_service):
    class BoomLLM(LLMProvider):
        @property
        def model_name(self) -> str:
            return "fake-model"

        async def generate(self, *a, **kw):
            raise RuntimeError("connection reset")

        async def health_check(self) -> bool:
            return False

    orchestrator = OrchestratorAgent(
        llm=BoomLLM(), specialists={"reservation": FakeSpecialist("reservation", [])}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)

    assert result.status == "error"
    assert result.error is not None
