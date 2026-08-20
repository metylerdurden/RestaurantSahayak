"""Manager API reservations CRUD against the real app + real Postgres. No LLM is
involved for any of these routes — proves the direct-service path, including that
a high-impact change still gets gated behind an Approval exactly like the agent
path does, and that the manager-initiated mutation is attributed to a
"manager_dashboard" AgentRun (see app.api.manager_context) rather than skipping
attribution because no agent was involved."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from tests.integration.api_helpers import build_test_app
from tests.integration.factories import future, make_customer, make_reservation, make_restaurant, make_table, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_list_reservations_filters_by_restaurant(client, db_session):
    restaurant = await make_restaurant(db_session)
    other_restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant)
    reservation = await make_reservation(db_session, restaurant, customer, table)
    await make_reservation(
        db_session,
        other_restaurant,
        await make_customer(db_session, other_restaurant),
        await make_table(db_session, other_restaurant),
    )

    response = await client.get(f"/api/v1/reservations?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    ids = [r["id"] for r in response.json()]
    assert ids == [str(reservation.id)]


async def test_create_reservation_succeeds_and_is_attributed_to_a_manager_run(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)

    response = await client.post(
        "/api/v1/reservations",
        json={
            "restaurant_id": str(restaurant.id),
            "customer_id": str(customer.id),
            "party_size": 2,
            "requested_time": future().isoformat(),
            "table_id": str(table.id),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["reservation"]["party_size"] == 2

    runs = await client.get(f"/api/v1/agent-runs?restaurant_id={restaurant.id}")
    assert any(r["agent_name"] == "manager_dashboard" and r["status"] == "completed" for r in runs.json())


async def test_create_reservation_with_unknown_customer_returns_422(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)

    response = await client.post(
        "/api/v1/reservations",
        json={
            "restaurant_id": str(restaurant.id),
            "customer_id": "00000000-0000-0000-0000-000000000000",
            "party_size": 2,
            "requested_time": future().isoformat(),
        },
    )

    assert response.status_code == 422


async def test_modify_small_party_reservation_applies_immediately(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    reservation = await make_reservation(db_session, restaurant, customer, table, party_size=2)

    response = await client.patch(
        f"/api/v1/reservations/{reservation.id}",
        json={"restaurant_id": str(restaurant.id), "notes": "Window seat please"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["reservation"]["notes"] == "Window seat please"


async def test_modify_high_impact_reservation_requires_approval(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=10)
    reservation = await make_reservation(db_session, restaurant, customer, table, party_size=8)

    response = await client.patch(
        f"/api/v1/reservations/{reservation.id}",
        json={"restaurant_id": str(restaurant.id), "notes": "Move to patio"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_id"]

    approvals = await client.get(f"/api/v1/approvals?restaurant_id={restaurant.id}")
    assert any(a["id"] == body["approval_id"] for a in approvals.json())


async def test_cancel_small_party_reservation_applies_immediately(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    reservation = await make_reservation(db_session, restaurant, customer, table, party_size=2)

    response = await client.delete(
        f"/api/v1/reservations/{reservation.id}?restaurant_id={restaurant.id}&reason=Change+of+plans"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["reservation"]["status"] == "cancelled"


async def test_cancel_high_impact_reservation_requires_approval(client, db_session):
    restaurant = await make_restaurant(db_session)
    await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=10)
    reservation = await make_reservation(db_session, restaurant, customer, table, party_size=8)

    response = await client.delete(f"/api/v1/reservations/{reservation.id}?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
