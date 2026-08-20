"""The manual-trigger workflow API routes against the real app and real Postgres,
with the LLM/embedding providers swapped for fast, deterministic fakes (via
app.api.stack, the same module the routes use to build the agent stack) — no real
Ollama needed here; that's covered by the live tests. Proves the developer-facing
"trigger any workflow right now" surface actually works end to end through HTTP."""

from __future__ import annotations

import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager

import app.api.stack as stack_module
from app.api.deps import get_db_session
from app.llm.base import LLMProvider, LLMResponse, ToolCall
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
    return LLMResponse(
        content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)]
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake-model")


@pytest.fixture
async def client(db_session, monkeypatch):
    scripted_llm = ScriptedLLM(
        [
            _tool_call("c0", "get_staff_schedule", {"date_from": "2026-01-01", "date_to": "2026-01-02"}),
            _tool_call(
                "c1",
                "calculate_staff_requirement",
                {"start_at": "2026-01-01T18:00:00+00:00", "end_at": "2026-01-01T22:00:00+00:00"},
            ),
            _final("Staffing looks adequate for the checked window."),
        ]
    )
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


async def test_trigger_unknown_workflow_type_returns_404(client, db_session):
    restaurant = await make_restaurant(db_session)
    response = await client.post(f"/workflows/not_a_real_workflow/trigger?restaurant_id={restaurant.id}")
    assert response.status_code == 404


async def test_trigger_staffing_monitoring_runs_it_now_and_returns_the_result(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_staff(db_session, restaurant, role="server", name="Alex")

    response = await client.post(f"/workflows/staffing_monitoring/trigger?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_type"] == "staffing_monitoring"
    assert body["triggered_by"] == "manual"
    assert body["status"] == "completed"
    assert body["final_result"]["status"] == "completed"


async def test_get_workflow_run_detail_after_triggering(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_staff(db_session, restaurant, role="server", name="Priya")

    trigger_response = await client.post(f"/workflows/staffing_monitoring/trigger?restaurant_id={restaurant.id}")
    workflow_run_id = trigger_response.json()["id"]

    detail_response = await client.get(f"/workflows/runs/{workflow_run_id}")

    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["workflow_run"]["id"] == workflow_run_id
    assert any(r["agent_name"] == "staffing" for r in body["agent_runs"])
    assert any(tc["tool_name"] == "get_staff_schedule" for tc in body["tool_calls"])


async def test_get_unknown_workflow_run_returns_404(client):
    response = await client.get(f"/workflows/runs/{uuid.uuid4()}")
    assert response.status_code == 404
