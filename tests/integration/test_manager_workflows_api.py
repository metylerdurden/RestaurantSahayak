"""Manager API named workflow triggers: POST /api/v1/workflows/daily-briefing,
/inventory-check, /staffing-check — against the real app, exercising the exact same
BackgroundWorkflow instances the scheduler (Step 17) uses."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.llm.base import LLMResponse, ToolCall
from tests.integration.api_helpers import build_test_app, final
from tests.integration.factories import make_restaurant

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


async def test_trigger_daily_briefing(db_session, monkeypatch):
    restaurant = await make_restaurant(db_session)
    responses = [
        _identify_domains("reservation"),
        _delegate("c0", "reservation", "What's booked tonight?"),
        final("Nothing booked yet."),
        _finish(),
        final("Quiet night ahead — nothing booked yet."),
    ]
    app = build_test_app(db_session, monkeypatch, responses=responses)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(f"/api/v1/workflows/daily-briefing?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_type"] == "daily_briefing"
    assert body["status"] == "completed"
    assert "Quiet night" in body["final_result"]["briefing"]


async def test_trigger_inventory_check(db_session, monkeypatch):
    restaurant = await make_restaurant(db_session)
    app = build_test_app(db_session, monkeypatch, responses=[final("Everything is adequately stocked.")])
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(f"/api/v1/workflows/inventory-check?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_type"] == "inventory_monitoring"
    assert body["status"] == "completed"


async def test_trigger_staffing_check(db_session, monkeypatch):
    restaurant = await make_restaurant(db_session)
    app = build_test_app(db_session, monkeypatch, responses=[final("Staffing looks adequate.")])
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(f"/api/v1/workflows/staffing-check?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_type"] == "staffing_monitoring"
    assert body["status"] == "completed"
