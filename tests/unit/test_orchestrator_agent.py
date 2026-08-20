"""OrchestratorAgent against a scripted LLMProvider and fake specialist agents — no
real LLM, no database, no real specialist logic. Proves the LangGraph wiring itself:
delegation loop, multi-agent sequencing, state passed to each specialist
(parent_run_id/correlation_id linking), retry-on-error, the max-delegations guard,
pending-approval pause/resume, and graceful handling of malformed/unrecognized
routing decisions. Real Qwen3-8B routing/combination behavior against real specialist
agents is exercised in tests/integration/test_orchestrator_agent.py (real DB,
scripted LLMs) and tests/integration/test_orchestrator_agent_live.py (real model)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.state import AgentResult
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.repositories.agent_run_repo import AgentRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService

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
        content="",
        model="fake-model",
        tool_calls=[
            ToolCall(id=call_id, name="delegate", arguments={"agent_name": agent_name, "instruction": instruction})
        ],
    )


def _finish(call_id: str = "finish") -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name="finish", arguments={})])


def _identify_domains(*domains: str) -> LLMResponse:
    """The first LLM call of every orchestrator run: _identify_scope_node asking
    which domains are essential, before any delegation happens."""
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[ToolCall(id="scope", name="identify_required_domains", arguments={"domains": list(domains)})],
    )


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
        _identify_domains("reservation"),
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
async def test_handle_never_raises_even_if_recording_completion_fails(agent_run_service):
    """Step 20 reliability hardening: persisting the final AgentRun status (a
    database write) must never stop handle() from returning a coherent
    OrchestratorResult to its caller — e.g. if the database became unreachable
    right at the end of an otherwise-successful run."""
    agent_run_service.complete_run = AsyncMock(side_effect=RuntimeError("database unreachable"))
    reservation = FakeSpecialist("reservation", [_result(summary="Nothing booked yet.")])
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "What's booked?"),
        _finish(),
        _final("Nothing booked yet tonight."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"reservation": reservation}, agent_run_service=agent_run_service
    )

    result = await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)  # must not raise

    assert result.status == "completed"
    assert result.summary == "Nothing booked yet tonight."


@pytest.mark.asyncio
async def test_multi_agent_readiness_check_invokes_several_specialists_in_sequence(agent_run_service):
    reservation = FakeSpecialist("reservation", [_result(summary="12 covers booked tonight across 3 reservations.")])
    inventory = FakeSpecialist("inventory", [_result(summary="All key items are in stock.")])
    staffing = FakeSpecialist("staffing", [_result(summary="Tonight's shift is adequately staffed.")])
    analytics = FakeSpecialist("analytics", [_result(summary="Similar nights typically run smoothly.")])

    responses = [
        _identify_domains("reservation", "inventory", "staffing"),
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
            "reservation": reservation,
            "inventory": inventory,
            "staffing": staffing,
            "analytics": analytics,
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
        _identify_domains("customer", "reservation"),
        _delegate("c0", "customer", "Look up Raj and any known preferences."),
        _delegate(
            "c1",
            "reservation",
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
async def test_pending_approval_pauses_the_graph_instead_of_finishing(agent_run_service):
    approval_id = uuid.uuid4()
    reservation = FakeSpecialist(
        "reservation",
        [
            _result(
                status="pending_approval",
                summary="Cancelling this large party requires manager approval.",
                data={"approval_id": str(approval_id), "status": "pending_approval", "summary": "..."},
            )
        ],
    )
    # Only the scope call and one decide turn should ever be consumed — the graph
    # must pause at await_approval before decide() is called again.
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "Cancel the reservation for the party of 8."),
    ]
    approval_service = AsyncMock(spec=ApprovalService)
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"reservation": reservation},
        agent_run_service=agent_run_service,
        approval_service=approval_service,
    )

    result = await orchestrator.handle("Cancel the party of 8's reservation.", restaurant_id=RESTAURANT_ID)

    assert result.status == "pending_approval"
    assert result.awaiting_approval_id == approval_id
    assert len(result.pending_approvals) == 1
    assert result.pending_approvals[0].agent_name == "reservation"
    approval_service.approve.assert_not_called()
    approval_service.reject.assert_not_called()


@pytest.mark.asyncio
async def test_resume_after_approval_executes_and_completes_the_workflow(agent_run_service):
    approval_id = uuid.uuid4()
    reservation = FakeSpecialist(
        "reservation",
        [
            _result(
                status="pending_approval",
                summary="Needs approval.",
                data={"approval_id": str(approval_id), "status": "pending_approval", "summary": "Needs approval."},
            )
        ],
    )
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "Cancel the reservation for the party of 8."),
        _finish(),
        _final("The cancellation was approved and executed."),
    ]
    approval_service = AsyncMock(spec=ApprovalService)
    approval_service.approve.return_value = SimpleNamespace(
        reason="Cancel reservation for party of 8",
        execution_result={"status": "success", "result": {"status": "cancelled"}},
    )
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"reservation": reservation},
        agent_run_service=agent_run_service,
        approval_service=approval_service,
    )

    paused = await orchestrator.handle("Cancel the party of 8's reservation.", restaurant_id=RESTAURANT_ID)
    assert paused.status == "pending_approval"

    decided_by = uuid.uuid4()
    resumed = await orchestrator.resume(paused.orchestrator_run_id, decision="approved", decided_by_user_id=decided_by)

    assert resumed.status == "completed"
    assert "approved and executed" in resumed.summary.lower()
    approval_service.approve.assert_called_once_with(approval_id, decided_by)


@pytest.mark.asyncio
async def test_resume_after_rejection_does_not_execute(agent_run_service):
    approval_id = uuid.uuid4()
    reservation = FakeSpecialist(
        "reservation",
        [
            _result(
                status="pending_approval",
                summary="Needs approval.",
                data={"approval_id": str(approval_id), "status": "pending_approval", "summary": "Needs approval."},
            )
        ],
    )
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "Cancel the reservation for the party of 8."),
        _finish(),
        _final("The manager rejected the cancellation; the reservation stands."),
    ]
    approval_service = AsyncMock(spec=ApprovalService)
    approval_service.reject.return_value = SimpleNamespace(
        reason="Cancel reservation for party of 8", execution_result=None
    )
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"reservation": reservation},
        agent_run_service=agent_run_service,
        approval_service=approval_service,
    )

    paused = await orchestrator.handle("Cancel the party of 8's reservation.", restaurant_id=RESTAURANT_ID)
    decided_by = uuid.uuid4()
    resumed = await orchestrator.resume(paused.orchestrator_run_id, decision="rejected", decided_by_user_id=decided_by)

    assert resumed.status == "completed"
    assert "rejected" in resumed.summary.lower()
    approval_service.reject.assert_called_once_with(approval_id, decided_by)
    approval_service.approve.assert_not_called()


@pytest.mark.asyncio
async def test_pending_approval_without_approval_service_configured_errors_safely(agent_run_service):
    approval_id = uuid.uuid4()
    reservation = FakeSpecialist(
        "reservation",
        [
            _result(
                status="pending_approval",
                summary="Needs approval.",
                data={"approval_id": str(approval_id), "status": "pending_approval", "summary": "Needs approval."},
            )
        ],
    )
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "Cancel the reservation for the party of 8."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"reservation": reservation},
        agent_run_service=agent_run_service,
    )  # no approval_service

    paused = await orchestrator.handle("Cancel the party of 8's reservation.", restaurant_id=RESTAURANT_ID)
    assert paused.status == "pending_approval"

    resumed = await orchestrator.resume(
        paused.orchestrator_run_id, decision="approved", decided_by_user_id=uuid.uuid4()
    )
    assert resumed.status == "error"


@pytest.mark.asyncio
async def test_failed_specialist_is_retried_once_then_accepted(agent_run_service):
    inventory = FakeSpecialist(
        "inventory",
        [
            _result(status="error", summary="internal error"),
            _result(status="completed", summary="Stock levels look fine."),
        ],
    )
    responses = [
        _identify_domains("inventory"),
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
        _identify_domains("inventory"),
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
    responses = [_identify_domains("reservation")]
    responses.extend(_delegate(f"c{i}", "reservation", f"task {i}") for i in range(10))
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
        _identify_domains(),
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[
                ToolCall(id="c0", name="delegate", arguments={"agent_name": "not_a_real_agent", "instruction": "x"})
            ],
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
        llm=BoomLLM(),
        specialists={"reservation": FakeSpecialist("reservation", [])},
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)

    assert result.status == "error"
    assert result.error is not None


@pytest.mark.asyncio
async def test_finish_is_forced_to_wait_until_every_required_domain_has_been_covered(agent_run_service):
    """The deterministic completeness guarantee from _decide_node/_force_delegate:
    even if the decide call keeps choosing finish() before every domain
    _identify_scope_node marked essential has been consulted, the orchestrator must
    not accept that finish() — it must override it and force delegation to the next
    missing domain instead, until all of them are covered."""
    reservation = FakeSpecialist("reservation", [_result(summary="3 reservations tonight.")])
    inventory = FakeSpecialist("inventory", [_result(summary="Stock levels look fine.")])
    staffing = FakeSpecialist("staffing", [_result(summary="Fully staffed tonight.")])

    # The decide call tries to finish() every single time it's asked — it never once
    # calls delegate() itself. If the orchestrator only trusted the model's own
    # per-turn judgment, this would finish immediately with zero invocations.
    responses = [
        _identify_domains("reservation", "inventory", "staffing"),
        _finish(),
        _finish(),
        _finish(),
        _finish(),
        _final("Tonight looks ready across the board."),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses),
        specialists={"reservation": reservation, "inventory": inventory, "staffing": staffing},
        agent_run_service=agent_run_service,
    )

    result = await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)

    assert result.status == "completed"
    # All three required domains were forced in, in the order they were declared
    # missing, despite the model never once calling delegate().
    assert [inv.agent_name for inv in result.invocations] == ["reservation", "inventory", "staffing"]
    assert len(reservation.calls) == 1
    assert len(inventory.calls) == 1
    assert len(staffing.calls) == 1
