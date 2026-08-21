"""Step 18 (Observability): verifies each of the instrumented operations actually
produces an OpenTelemetry span with the attributes the spec calls for — tool calls,
specialist/orchestrator agent runs, LLM calls (with token usage), memory operations,
event publish/handle, approval decisions, and background workflow runs. Each test
reuses the same mocked-repository/scripted-LLM fixtures already established by this
project's other unit tests for the module in question (no real database, no real
model) — the goal here is purely "does the span exist with the right shape," not
re-proving business logic already covered elsewhere. Captured via the session-wide
InMemorySpanExporter installed by tests/conftest.py (the `otel_spans` fixture)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agents.customer_agent import CustomerAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.state import AgentResult
from app.embeddings.base import EmbeddingProvider
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.llm.ollama_provider import OllamaLLMProvider
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.event_repo import EventRepository
from app.repositories.memory_repo import MemoryRepository
from app.repositories.workflow_run_repo import WorkflowRunRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.event_bus import InProcessEventBus
from app.services.memory_service import MemoryService
from app.tools.customer_tools import GetCustomerTool
from app.workflows.background_workflow import BackgroundWorkflow

RESTAURANT_ID = uuid.uuid4()

pytestmark = pytest.mark.asyncio


def _spans_by_name(otel_spans):
    return {s.name: s for s in otel_spans.get_finished_spans()}


# --- tool.call + <agent>.run (via a real ToolCallingAgent subclass) ---


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


async def test_agent_run_and_tool_call_spans_are_created_and_nested(agent_run_service, otel_spans):
    customer_service = AsyncMock(spec=CustomerService)
    customer_service.get_customer.return_value = [
        SimpleNamespace(id=uuid.uuid4(), name="Raj Patel", phone="+15551239999", email=None, is_active=True)
    ]
    responses = [
        LLMResponse(
            content="",
            model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_customer", arguments={"query": "Raj"})],
        ),
        LLMResponse(content="Found Raj.", model="fake-model"),
    ]
    agent = CustomerAgent(
        llm=ScriptedLLM(responses), tools=[GetCustomerTool(customer_service)], agent_run_service=agent_run_service
    )

    await agent.handle("Find Raj", restaurant_id=RESTAURANT_ID)

    spans = _spans_by_name(otel_spans)
    assert "customer_agent.run" in spans
    assert "tool.call" in spans

    agent_span = spans["customer_agent.run"]
    assert agent_span.attributes["agent_name"] == "customer"
    assert agent_span.attributes["model_name"] == "fake-model"
    assert agent_span.attributes["restaurant_id"] == str(RESTAURANT_ID)
    assert uuid.UUID(agent_span.attributes["correlation_id"])  # a real id, distinct from agent_run_id
    assert uuid.UUID(agent_span.attributes["agent_run_id"])  # this run's own id, not just its correlation_id
    assert agent_span.attributes["success"] is True
    assert agent_span.attributes["status"] == "completed"
    assert "latency_ms" in agent_span.attributes

    tool_span = spans["tool.call"]
    assert tool_span.attributes["tool_name"] == "get_customer"
    assert tool_span.attributes["agent_name"] == "customer"
    assert tool_span.attributes["success"] is True
    # The tool call's agent_run_id matches the agent run it belongs to — lets a
    # trace backend filter every span for one specific run, not just everything
    # sharing its (broader) correlation_id.
    assert tool_span.attributes["agent_run_id"] == agent_span.attributes["agent_run_id"]
    # tool.call happened inside the agent run it belongs to.
    assert tool_span.parent.span_id == agent_span.context.span_id


# --- orchestrator.run ---


class FakeSpecialist:
    def __init__(self, name: str, results: list[AgentResult]) -> None:
        self.name = name
        self._results = list(results)

    async def handle(self, task: str, **kwargs) -> AgentResult:
        return self._results.pop(0)


def _delegate(call_id: str, agent_name: str, instruction: str) -> LLMResponse:
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[
            ToolCall(id=call_id, name="delegate", arguments={"agent_name": agent_name, "instruction": instruction})
        ],
    )


def _finish() -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id="f", name="finish", arguments={})])


def _identify_domains(*domains: str) -> LLMResponse:
    return LLMResponse(
        content="",
        model="fake-model",
        tool_calls=[ToolCall(id="scope", name="identify_required_domains", arguments={"domains": list(domains)})],
    )


async def test_orchestrator_run_span_is_created_with_delegation_count(agent_run_service, otel_spans):
    reservation = FakeSpecialist(
        "reservation",
        [
            AgentResult(
                agent_run_id=uuid.uuid4(),
                status="completed",
                summary="3 reservations tonight.",
                tool_calls=[],
                data=None,
                error=None,
                latency_ms=5,
            )
        ],
    )
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "How many reservations tonight?"),
        _finish(),
        LLMResponse(content="3 reservations tonight.", model="fake-model"),
    ]
    orchestrator = OrchestratorAgent(
        llm=ScriptedLLM(responses), specialists={"reservation": reservation}, agent_run_service=agent_run_service
    )

    await orchestrator.handle("Are we ready for tonight?", restaurant_id=RESTAURANT_ID)

    spans = _spans_by_name(otel_spans)
    assert "orchestrator.run" in spans
    span = spans["orchestrator.run"]
    assert span.attributes["agent_name"] == "orchestrator"
    assert span.attributes["restaurant_id"] == str(RESTAURANT_ID)
    assert span.attributes["success"] is True
    assert span.attributes["delegation_count"] == 1


# --- llm.call (the real Ollama provider, HTTP mocked) ---


async def test_llm_call_span_captures_model_and_token_usage(monkeypatch, otel_spans):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {"content": "hello"},
                "prompt_eval_count": 42,
                "eval_count": 7,
            },
        )

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler), *args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    await provider.generate([LLMMessage(role="user", content="hi")])

    spans = _spans_by_name(otel_spans)
    assert "llm.call" in spans
    span = spans["llm.call"]
    assert span.attributes["model_name"] == "qwen3:8b"
    assert span.attributes["model_provider"] == "ollama"
    assert span.attributes["success"] is True
    assert span.attributes["llm.prompt_tokens"] == 42
    assert span.attributes["llm.completion_tokens"] == 7


# --- memory.search / memory.write ---


class FakeEmbeddings(EmbeddingProvider):
    @property
    def model_name(self) -> str:
        return "fake-bge-m3"

    @property
    def dimension(self) -> int:
        return 8

    def embed(self, texts):
        return [[0.1] * 8 for _ in texts]

    def health_check(self) -> bool:
        return True


def _memory_row(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        restaurant_id=RESTAURANT_ID,
        customer_id=None,
        agent_name=None,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a window table."},
        embedding=[0.1] * 8,
        importance=3,
        confidence=Decimal("1.00"),
        source="manager_stated",
        is_active=True,
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_memory_write_and_search_spans_carry_the_operation_not_the_content(otel_spans):
    repo = AsyncMock(spec=MemoryRepository)
    repo.get_active_by_scope_topic.return_value = None
    repo.create.side_effect = lambda **kwargs: _memory_row(**kwargs)
    repo.save.side_effect = lambda m: m
    repo.touch_access.side_effect = lambda m: m
    repo.search_by_embedding.return_value = [(_memory_row(), 0.05)]
    service = MemoryService(repo, FakeEmbeddings())

    await service.add_memory(
        restaurant_id=RESTAURANT_ID,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a quiet table."},
        source="manager_stated",
    )
    await service.search_memory(restaurant_id=RESTAURANT_ID, query="seating preference")

    write_spans = [s for s in otel_spans.get_finished_spans() if s.name == "memory.write"]
    search_spans = [s for s in otel_spans.get_finished_spans() if s.name == "memory.search"]
    assert len(write_spans) == 1
    assert len(search_spans) == 1
    assert write_spans[0].attributes["memory_operation"] == "add"
    assert write_spans[0].attributes["memory_type"] == "CUSTOMER_PREFERENCE"
    assert write_spans[0].attributes["success"] is True
    assert search_spans[0].attributes["memory_operation"] == "search"
    assert search_spans[0].attributes["result_count"] == 1
    for span in write_spans + search_spans:
        assert "content" not in span.attributes
        assert "topic" not in span.attributes
        assert "query" not in span.attributes


# --- event.publish / event.handle ---


async def test_event_publish_and_handle_spans_are_created_and_nested(otel_spans):
    def _event_row(**overrides):
        from datetime import datetime, timezone

        defaults = dict(
            id=uuid.uuid4(),
            event_type="reservation.created",
            restaurant_id=RESTAURANT_ID,
            entity_id=uuid.uuid4(),
            payload={"party_size": 4},
            correlation_id=uuid.uuid4(),
            published_by="reservation_service",
            idempotency_key=None,
            created_at=datetime.now(timezone.utc),
            handled=False,
            handled_at=None,
            handler_results=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    repo = AsyncMock(spec=EventRepository)
    repo.create.side_effect = lambda **kwargs: _event_row(**kwargs)
    repo.save.side_effect = lambda event: event
    bus = InProcessEventBus(repo)

    async def handler(event):
        return None

    bus.subscribe("reservation.created", handler, name="test_handler")
    await bus.publish(
        event_type="reservation.created",
        restaurant_id=RESTAURANT_ID,
        entity_id=uuid.uuid4(),
        payload={"party_size": 4},
        published_by="reservation_service",
    )

    spans = _spans_by_name(otel_spans)
    assert "event.publish" in spans
    assert "event.handle" in spans
    publish_span = spans["event.publish"]
    handle_span = spans["event.handle"]
    assert publish_span.attributes["event_type"] == "reservation.created"
    assert publish_span.attributes["success"] is True
    assert handle_span.attributes["handler"] == "test_handler"
    assert handle_span.attributes["success"] is True
    assert handle_span.parent.span_id == publish_span.context.span_id


# --- approval.create / approve / reject ---


def _approval(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        restaurant_id=RESTAURANT_ID,
        domain="reservation",
        action="cancel_reservation",
        agent_name="reservation",
        proposed_by_agent_run_id=uuid.uuid4(),
        reason="Cancel a large party",
        risk_level="MEDIUM",
        status="pending",
        execution_result=None,
    )
    defaults.update(overrides)
    return type("Approval", (), defaults)()


async def test_approval_create_and_approve_spans_carry_status(otel_spans):
    repo = AsyncMock(spec=ApprovalRepository)
    created = _approval()
    repo.create.return_value = created
    repo.get_by_id.return_value = created
    repo.save.side_effect = lambda a: a
    service = ApprovalService(repo)

    await service.create_approval_request(
        restaurant_id=RESTAURANT_ID,
        domain="reservation",
        action="cancel_reservation",
        agent_name="reservation",
        proposed_by_agent_run_id=uuid.uuid4(),
        parameters={},
        reason="Cancel a large party",
        risk_level="MEDIUM",
    )
    created.status = "pending"
    await service.approve(created.id, uuid.uuid4())

    spans = _spans_by_name(otel_spans)
    assert "approval.create" in spans
    assert "approval.approve" in spans
    assert spans["approval.create"].attributes["approval_status"] == "pending"
    assert spans["approval.approve"].attributes["approval_status"] == "approved"
    assert spans["approval.approve"].attributes["success"] is True


# --- workflow.run ---


class FakeWorkflow(BackgroundWorkflow):
    workflow_type = "daily_briefing"

    async def _execute(self, *, restaurant_id, correlation_id):
        return {"summary": "all good"}


async def test_background_workflow_run_span_is_created(otel_spans):
    repo = AsyncMock(spec=WorkflowRunRepository)
    repo.create.side_effect = lambda **kwargs: SimpleNamespace(
        id=uuid.uuid4(), completed_at=None, final_result=None, error=None, **kwargs
    )
    repo.save.side_effect = lambda run: run
    workflow = FakeWorkflow(workflow_run_repo=repo)

    await workflow.run(restaurant_id=RESTAURANT_ID, triggered_by="scheduler")

    spans = _spans_by_name(otel_spans)
    assert "workflow.run" in spans
    span = spans["workflow.run"]
    assert span.attributes["workflow_name"] == "daily_briefing"
    assert span.attributes["triggered_by"] == "scheduler"
    assert span.attributes["status"] == "completed"
    assert span.attributes["success"] is True
