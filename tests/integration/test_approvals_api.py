"""GET /api/v1/approvals and the approve/reject decision endpoints against the
real app — proves the Manager API's approval decisions go through the exact same
ApprovalService state machine as every other approval channel (Constitution IV):
a decided approval leaves "pending" and the decision sticks."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager

from tests.integration.api_helpers import build_test_app
from tests.integration.factories import make_agent_run, make_approval, make_restaurant, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db_session, monkeypatch):
    app = build_test_app(db_session, monkeypatch)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    app.dependency_overrides.clear()


async def test_list_pending_approvals(client, db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    run = await make_agent_run(db_session, restaurant, user)
    approval = await make_approval(db_session, restaurant, run)

    response = await client.get(f"/api/v1/approvals?restaurant_id={restaurant.id}")

    assert response.status_code == 200
    assert [a["id"] for a in response.json()] == [str(approval.id)]


async def test_approve_marks_approved_and_removes_it_from_pending(client, db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    run = await make_agent_run(db_session, restaurant, user)
    approval = await make_approval(db_session, restaurant, run)

    response = await client.post(f"/api/v1/approvals/{approval.id}/approve", json={"decided_by_user_id": str(user.id)})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    pending = await client.get(f"/api/v1/approvals?restaurant_id={restaurant.id}")
    assert pending.json() == []


async def test_reject_marks_rejected_with_reason(client, db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    run = await make_agent_run(db_session, restaurant, user)
    approval = await make_approval(db_session, restaurant, run)

    response = await client.post(
        f"/api/v1/approvals/{approval.id}/reject",
        json={"decided_by_user_id": str(user.id), "reason": "Not enough staff tonight"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_deciding_an_already_decided_approval_returns_409(client, db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    run = await make_agent_run(db_session, restaurant, user)
    approval = await make_approval(db_session, restaurant, run)

    first = await client.post(f"/api/v1/approvals/{approval.id}/approve", json={"decided_by_user_id": str(user.id)})
    assert first.status_code == 200

    second = await client.post(f"/api/v1/approvals/{approval.id}/reject", json={"decided_by_user_id": str(user.id)})
    assert second.status_code == 409
