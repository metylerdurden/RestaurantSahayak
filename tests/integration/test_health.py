"""Integration tests against the real app, real (local) database, and — for
/health/ready — a real Ollama server. Requires: Postgres reachable at
Settings.database_url, Ollama reachable at Settings.ollama_base_url with the
configured model pulled."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac


@pytest.mark.asyncio
async def test_health_live_always_ok(client: httpx.AsyncClient):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_reports_database_and_llm_checks(client: httpx.AsyncClient):
    response = await client.get("/health/ready")
    body = response.json()
    assert "database" in body["checks"]
    assert "llm" in body["checks"]
    assert response.status_code in (200, 503)


@pytest.mark.asyncio
async def test_correlation_id_is_returned_on_every_response(client: httpx.AsyncClient):
    response = await client.get("/health/live")
    assert "X-Correlation-Id" in response.headers


@pytest.mark.asyncio
async def test_correlation_id_is_propagated_when_provided(client: httpx.AsyncClient):
    response = await client.get("/health/live", headers={"X-Correlation-Id": "test-fixed-id"})
    assert response.headers["X-Correlation-Id"] == "test-fixed-id"
