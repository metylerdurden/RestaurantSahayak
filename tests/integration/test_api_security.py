"""Step 20 security hardening: the API_KEY authorization boundary
(app.api.security.require_api_key). Proves both halves of the contract — unset,
every route stays exactly as unauthenticated as before (no regression for local
dev or the rest of the test suite); set, every Manager API route demands a
matching X-API-Key header, while /health stays open for infra probes."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.core.config import get_settings
from tests.integration.api_helpers import build_test_app
from tests.integration.factories import make_restaurant

pytestmark = pytest.mark.asyncio


async def test_api_key_unset_leaves_routes_unauthenticated(db_session, monkeypatch):
    """The default (local dev, and every other test in this suite) — no key
    configured, no header required."""
    restaurant = await make_restaurant(db_session)
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/api/v1/dashboard?restaurant_id={restaurant.id}")
    assert response.status_code == 200


async def test_api_key_set_rejects_missing_or_wrong_key(db_session, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-shared-secret")
    get_settings.cache_clear()
    restaurant = await make_restaurant(db_session)
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            missing = await client.get(f"/api/v1/dashboard?restaurant_id={restaurant.id}")
            wrong = await client.get(
                f"/api/v1/dashboard?restaurant_id={restaurant.id}", headers={"X-API-Key": "not-the-secret"}
            )
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_api_key_set_accepts_the_correct_key(db_session, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-shared-secret")
    get_settings.cache_clear()
    restaurant = await make_restaurant(db_session)
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                f"/api/v1/dashboard?restaurant_id={restaurant.id}",
                headers={"X-API-Key": "test-shared-secret"},
            )
    assert response.status_code == 200


async def test_health_stays_open_even_when_api_key_is_set(db_session, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-shared-secret")
    get_settings.cache_clear()
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health/live")
    assert response.status_code == 200
