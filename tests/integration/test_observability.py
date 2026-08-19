"""End-to-end observability against the real FastAPI app and real Postgres (LLM/
embedding providers swapped for fast, deterministic fakes — the same pattern as
tests/integration/test_workflows_api.py). Proves the pieces unit tests can't: an
inbound HTTP request becomes a root span tagged with the request's own correlation
id (app.main's middleware), and triggering a background workflow through that
request produces a real "restaurant.readiness_check"-shaped nested trace —
workflow.run -> staffing_agent.run -> tool.call — exactly the shape Step 18 asks
for, built from real repositories/services rather than mocks."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.api.stack as stack_module
from app.api.deps import get_db_session
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.main import create_app
from tests.integration.factories import make_restaurant, make_staff

pytestmark = pytest.mark.asyncio


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
        if not self._responses:
            return LLMResponse(content="Done.", model="fake-model")
        return self._responses.pop(0)

    async def health_check(self) -> bool:
        return True


class FakeEmbeddingProvider:
    @property
    def model_name(self) -> str:
        return "fake-embeddings"

    @property
    def dimension(self) -> int:
        return 8

    def embed(self, texts):
        return [[0.1] * 8 for _ in texts]

    def health_check(self) -> bool:
        return True


def _tool_call(call_id: str, name: str, arguments: dict) -> LLMResponse:
    return LLMResponse(content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake-model")


@pytest.fixture
async def client(db_session, monkeypatch):
    scripted_llm = ScriptedLLM([
        _tool_call("c0", "get_staff_schedule", {"date_from": "2026-01-01", "date_to": "2026-01-02"}),
        _tool_call("c1", "calculate_staff_requirement", {"start_at": "2026-01-01T18:00:00+00:00", "end_at": "2026-01-01T22:00:00+00:00"}),
        _final("Staffing looks adequate for the checked window."),
    ])
    monkeypatch.setattr(stack_module, "get_llm_provider", lambda: scripted_llm)
    monkeypatch.setattr(stack_module, "get_embedding_provider", lambda: FakeEmbeddingProvider())

    app = create_app()

    async def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

    app.dependency_overrides.clear()


async def test_triggering_a_workflow_over_http_produces_a_nested_trace(client, db_session, otel_spans):
    restaurant = await make_restaurant(db_session)
    await make_staff(db_session, restaurant, role="server", name="Alex")

    response = await client.post(
        f"/workflows/staffing_monitoring/trigger?restaurant_id={restaurant.id}",
        headers={"X-Correlation-Id": "test-correlation-abc"},
    )
    assert response.status_code == 200

    spans = {s.name: s for s in otel_spans.get_finished_spans()}
    # FastAPIInstrumentor also emits per-ASGI-event child spans (e.g. "POST ... http
    # send") under the same route name — the request-level root span is the one
    # carrying the route's own "http.route" attribute.
    http_root_spans = [
        s for s in otel_spans.get_finished_spans()
        if s.name.startswith("POST") and "http.route" in s.attributes
    ]
    assert http_root_spans, "FastAPIInstrumentor should have created a root span for the request"
    assert http_root_spans[0].attributes.get("correlation_id") == "test-correlation-abc"

    assert "workflow.run" in spans
    assert "staffing_agent.run" in spans
    assert "tool.call" in spans

    workflow_span = spans["workflow.run"]
    agent_span = spans["staffing_agent.run"]
    tool_span = spans["tool.call"]

    assert workflow_span.attributes["workflow_name"] == "staffing_monitoring"
    assert workflow_span.attributes["triggered_by"] == "manual"
    assert workflow_span.attributes["success"] is True

    # Nesting matches the spec's example tree: workflow.run is the parent of the
    # agent run it caused, which is the parent of the tool call it made.
    assert agent_span.parent.span_id == workflow_span.context.span_id
    assert tool_span.parent.span_id == agent_span.context.span_id
