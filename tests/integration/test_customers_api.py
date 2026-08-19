"""GET /api/v1/customers, /{id}, and /{id}/memories against the real app."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.models import Memory
from tests.integration.api_helpers import build_test_app
from tests.integration.factories import make_customer, make_restaurant

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_list_customers(client, db_session):
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant, name="Raj Patel")

    response = await client.get(f"/api/v1/customers?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [str(customer.id)]


async def test_get_customer_by_id(client, db_session):
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant, name="Raj Patel")

    response = await client.get(f"/api/v1/customers/{customer.id}?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Raj Patel"


async def test_get_unknown_customer_returns_404(client, db_session):
    restaurant = await make_restaurant(db_session)

    response = await client.get(
        f"/api/v1/customers/00000000-0000-0000-0000-000000000000?restaurant_id={restaurant.id}"
    )

    assert response.status_code == 404


async def test_get_customer_memories(client, db_session):
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant, name="Raj Patel")
    db_session.add(
        Memory(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            memory_type="CUSTOMER_PREFERENCE",
            topic="seating_preference",
            content={"text": "Prefers a quiet table away from the kitchen."},
            source="manager_stated",
            importance=4,
        )
    )
    await db_session.flush()

    response = await client.get(f"/api/v1/customers/{customer.id}/memories?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    memories = response.json()
    assert len(memories) == 1
    assert memories[0]["topic"] == "seating_preference"
    assert "quiet table" in memories[0]["content"]["text"]
