"""GET /api/v1/inventory and /api/v1/inventory/alerts against the real app."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from tests.integration.api_helpers import build_test_app
from tests.integration.factories import make_inventory_item, make_restaurant

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_list_inventory_returns_every_item(client, db_session):
    restaurant = await make_restaurant(db_session)
    ok_item = await make_inventory_item(db_session, restaurant, quantity_on_hand=50, low_stock_threshold=5)
    low_item = await make_inventory_item(db_session, restaurant, quantity_on_hand=2, low_stock_threshold=5)

    response = await client.get(f"/api/v1/inventory?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    ids = {i["id"] for i in response.json()}
    assert ids == {str(ok_item.id), str(low_item.id)}


async def test_inventory_alerts_returns_only_low_and_out_of_stock(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_inventory_item(db_session, restaurant, quantity_on_hand=50, low_stock_threshold=5)
    low_item = await make_inventory_item(db_session, restaurant, quantity_on_hand=2, low_stock_threshold=5)
    out_item = await make_inventory_item(db_session, restaurant, quantity_on_hand=0, low_stock_threshold=5)

    response = await client.get(f"/api/v1/inventory/alerts?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    ids = {i["id"] for i in response.json()}
    assert ids == {str(low_item.id), str(out_item.id)}
