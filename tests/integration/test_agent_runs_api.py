"""Manager API Agent Activity surface: POST /api/v1/agent-runs (trigger), and the
list/get/trace read endpoints — against the real app, real Postgres, real
Orchestrator/specialist wiring, with the LLM scripted (fast, deterministic).
`test_trace_nests_orchestrator_over_specialist_over_tool` is the one that proves
the tree shape the Agent Activity view is built to render: Orchestrator ->
Specialist -> Tool -> Result."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.llm.base import LLMResponse, ToolCall
from tests.integration.api_helpers import build_test_app, final, tool_call
from tests.integration.factories import make_restaurant, make_user

pytestmark = pytest.mark.asyncio


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


async def test_trigger_a_specialist_agent_directly(db_session, monkeypatch):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    app = build_test_app(
        db_session,
        monkeypatch,
        responses=[tool_call("c0", "get_reservations", {}), final("No reservations found for that range.")],
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/agent-runs",
                json={"restaurant_id": str(restaurant.id), "agent_name": "reservation", "task": "Anything booked?"},
            )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert "No reservations" in body["summary"]


async def test_trigger_orchestrator_delegates_and_the_trace_nests_correctly(db_session, monkeypatch):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "What is booked?"),
        tool_call("c1", "get_reservations", {}),
        final("Nothing booked yet."),
        _finish(),
        final("The restaurant has nothing booked yet tonight."),
    ]
    app = build_test_app(db_session, monkeypatch, responses=responses)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            trigger = await client.post(
                "/api/v1/agent-runs",
                json={
                    "restaurant_id": str(restaurant.id),
                    "agent_name": "orchestrator",
                    "task": "Are we ready for tonight?",
                    "initiated_by_user_id": str(user.id),
                },
            )
            assert trigger.status_code == 200
            trigger_body = trigger.json()
            assert trigger_body["status"] == "completed"
            orchestrator_run_id = trigger_body["agent_run_id"]

            trace = await client.get(f"/api/v1/agent-runs/{orchestrator_run_id}/trace")
            assert trace.status_code == 200
            trace_body = trace.json()
            assert trace_body["agent_name"] == "orchestrator"
            assert len(trace_body["children"]) == 1
            specialist_node = trace_body["children"][0]
            assert specialist_node["agent_name"] == "reservation"
            tool_messages = [m for m in specialist_node["messages"] if m["role"] == "tool_result"]
            assert any(m["tool_name"] == "get_reservations" for m in tool_messages)

            listing = await client.get(f"/api/v1/agent-runs?restaurant_id={restaurant.id}")
            assert listing.status_code == 200
            assert any(r["id"] == orchestrator_run_id for r in listing.json())

            single = await client.get(f"/api/v1/agent-runs/{specialist_node['id']}")
            assert single.status_code == 200
            assert single.json()["agent_name"] == "reservation"
            assert single.json()["parent_run_id"] == orchestrator_run_id


async def test_get_unknown_agent_run_returns_404(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/agent-runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


async def test_http_correlation_id_is_threaded_into_the_agent_run(db_session, monkeypatch):
    """Part 2 audit fix: the correlation id set by the HTTP middleware (and echoed
    back on the X-Correlation-Id response header) must be the *same* id stored on
    the resulting AgentRun/spans — not a separate one AgentRunService minted on its
    own, which would make the response header useless for finding the run afterward."""
    import uuid as uuid_mod

    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    app = build_test_app(
        db_session,
        monkeypatch,
        responses=[tool_call("c0", "get_reservations", {}), final("No reservations found for that range.")],
    )
    sent_correlation_id = str(uuid_mod.uuid4())

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/agent-runs",
                json={"restaurant_id": str(restaurant.id), "agent_name": "reservation", "task": "Anything booked?"},
                headers={"X-Correlation-Id": sent_correlation_id},
            )
            assert response.status_code == 200
            assert response.headers["X-Correlation-Id"] == sent_correlation_id

            run_id = response.json()["agent_run_id"]
            run = await client.get(f"/api/v1/agent-runs/{run_id}")
            assert run.status_code == 200
            assert run.json()["correlation_id"] == sent_correlation_id


async def test_a_non_uuid_correlation_id_header_does_not_break_the_request(db_session, monkeypatch):
    """An external caller's X-Correlation-Id might not be a valid UUID (fine for log
    correlation) — the request must still succeed, with AgentRunService minting its
    own UUID rather than the request failing."""
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    app = build_test_app(
        db_session,
        monkeypatch,
        responses=[tool_call("c0", "get_reservations", {}), final("No reservations found for that range.")],
    )

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/agent-runs",
                json={"restaurant_id": str(restaurant.id), "agent_name": "reservation", "task": "Anything booked?"},
                headers={"X-Correlation-Id": "not-a-valid-uuid"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "completed"
