"""GET /api/v1/dashboard against the real app + real Postgres — proves every panel
the spec asks for (today's reservations, expected covers, inventory alerts,
staffing alerts, pending approvals, recent agent activity, daily briefing, recent
operational events) is actually assembled from real data, not stubbed."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.models import WorkflowRun
from tests.integration.api_helpers import build_test_app
from tests.integration.factories import (
    future,
    make_agent_run,
    make_approval,
    make_customer,
    make_event,
    make_inventory_item,
    make_reservation,
    make_shift_assignment,
    make_staff,
    make_staff_shift,
    make_table,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_dashboard_assembles_every_panel(client, db_session):
    from tests.integration.factories import make_restaurant

    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant)

    today_reservation = await make_reservation(
        db_session, restaurant, customer, table, party_size=4, requested_time=future(days=0, hour=19)
    )
    low_item = await make_inventory_item(db_session, restaurant, quantity_on_hand=1, low_stock_threshold=5)
    await make_inventory_item(
        db_session, restaurant, quantity_on_hand=50, low_stock_threshold=5
    )  # healthy, not an alert

    understaffed_shift = await make_staff_shift(
        db_session,
        restaurant,
        start_at=future(days=0, hour=18),
        end_at=future(days=0, hour=22),
        required_staff_count=4,
        status="understaffed",
    )
    server = await make_staff(db_session, restaurant)
    await make_shift_assignment(db_session, understaffed_shift, server)

    agent_run = await make_agent_run(db_session, restaurant, user)
    approval = await make_approval(db_session, restaurant, agent_run)
    await make_event(db_session, restaurant, event_type="reservation.created", entity_id=today_reservation.id)

    db_session.add(
        WorkflowRun(
            workflow_type="daily_briefing",
            restaurant_id=restaurant.id,
            correlation_id=agent_run.correlation_id,
            status="completed",
            triggered_by="manual",
            final_result={"briefing": "Quiet night expected."},
        )
    )
    await db_session.flush()

    response = await client.get(f"/api/v1/dashboard?restaurant_id={restaurant.id}")
    assert response.status_code == 200
    body = response.json()

    assert body["manager_user_id"] == str(user.id)
    assert body["expected_covers"] == 4
    assert [r["id"] for r in body["today_reservations"]] == [str(today_reservation.id)]
    assert str(low_item.id) in {i["id"] for i in body["inventory_alerts"]}
    assert len(body["inventory_alerts"]) == 1
    assert [s["id"] for s in body["staffing_alerts"]] == [str(understaffed_shift.id)]
    assert body["staffing_alerts"][0]["assignments"][0]["staff_id"] == str(server.id)
    assert [a["id"] for a in body["pending_approvals"]] == [str(approval.id)]
    assert any(r["id"] == str(agent_run.id) for r in body["recent_agent_activity"])
    assert body["daily_briefing"] == "Quiet night expected."
    assert any(e["event_type"] == "reservation.created" for e in body["recent_events"])


async def test_dashboard_handles_a_restaurant_with_no_activity_yet(client, db_session):
    from tests.integration.factories import make_restaurant

    restaurant = await make_restaurant(db_session)

    response = await client.get(f"/api/v1/dashboard?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["manager_user_id"] is None
    assert body["expected_covers"] == 0
    assert body["today_reservations"] == []
    assert body["inventory_alerts"] == []
    assert body["staffing_alerts"] == []
    assert body["pending_approvals"] == []
    assert body["daily_briefing"] is None
