"""Frontend/backend integration (Step 19): the manager dashboard's static assets
are actually served by the real app, and — critically — the StaticFiles mount at
"/" (registered last, so the dashboard can live at the site root) never shadows an
API route registered earlier. Also covers GET /api/v1/restaurants, the one
endpoint outside Step 19's explicit list that the frontend needs to avoid
requiring a manager to paste a restaurant UUID by hand."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from tests.integration.api_helpers import build_test_app
from tests.integration.factories import make_restaurant

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_dashboard_index_is_served_at_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "DineOps" in response.text


async def test_static_assets_are_served(client):
    js = await client.get("/static/app.js")
    css = await client.get("/static/style.css")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert css.status_code == 200
    assert "css" in css.headers["content-type"]


async def test_static_mount_does_not_shadow_api_routes(client, db_session):
    restaurant = await make_restaurant(db_session)
    response = await client.get(f"/api/v1/dashboard?restaurant_id={restaurant.id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


async def test_list_restaurants(client, db_session):
    restaurant = await make_restaurant(db_session)

    response = await client.get("/api/v1/restaurants")

    assert response.status_code == 200
    assert any(r["id"] == str(restaurant.id) for r in response.json())
