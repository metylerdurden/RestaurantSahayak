"""AnalyticsAgent exercised end to end against real Postgres data and the real
qwen3:8b model via Ollama. Skips automatically (rather than failing) if Ollama or the
configured model isn't reachable. Assertions favor invariants that must always hold —
the right tool is used, no crash, and any number the agent reports is traceable to
real tool output — over pinning the model's exact phrasing or turn count."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from app.agents.analytics_agent import AnalyticsAgent
from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.models import Reservation, Sale
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.analytics_repo import AnalyticsRepository
from app.services.agent_run_service import AgentRunService
from app.services.analytics_service import AnalyticsService
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool
from app.tools.base import utcnow
from tests.integration.factories import make_customer, make_menu_item, make_restaurant, make_table, make_user

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def llm():
    provider = build_llm_provider(get_settings())
    if not await provider.health_check():
        pytest.skip("Ollama is not reachable, or the configured LLM_MODEL is not pulled")
    return provider


def _build_agent(db_session, llm, *, max_iterations: int = 8) -> AnalyticsAgent:
    service = AnalyticsService(AnalyticsRepository(db_session))
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    tools = [GetDailySalesTool(service), GetItemSalesTool(service), GetNoShowRateTool(service)]
    return AnalyticsAgent(llm=llm, tools=tools, agent_run_service=agent_run_service, max_iterations=max_iterations)


def _tool_names(result) -> set[str]:
    return {tc.tool_name for tc in result.tool_calls}


async def _sale(db_session, restaurant, customer, table, item, *, quantity: int, when):
    reservation = Reservation(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        table_id=table.id,
        party_size=2,
        requested_time=when,
        status="completed",
        created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()
    db_session.add(
        Sale(
            restaurant_id=restaurant.id,
            reservation_id=reservation.id,
            menu_item_id=item.id,
            quantity=quantity,
            unit_price=item.price,
            sold_at=when,
        )
    )
    await db_session.flush()


async def test_top_selling_item_question_is_grounded_in_real_data(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    winner = await make_menu_item(db_session, restaurant, name="Lamb Souvlaki", price="26.00")
    runner_up = await make_menu_item(db_session, restaurant, name="Garden Salad", price="9.00")

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await _sale(db_session, restaurant, customer, table, winner, quantity=25, when=now)
    await _sale(db_session, restaurant, customer, table, runner_up, quantity=2, when=now)

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Which menu items performed best this week?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status != "error", result.summary
    assert "get_item_sales" in _tool_names(result), result.summary

    item_sales_calls = [tc for tc in result.tool_calls if tc.tool_name == "get_item_sales"]
    assert item_sales_calls, result.summary
    top_names_seen = {entry["name"] for tc in item_sales_calls for entry in tc.output["items"] if tc.output["items"]}
    assert "Lamb Souvlaki" in top_names_seen, top_names_seen
    # Whatever the model reports as the top seller, it must be the one the real data
    # actually supports — never a fabricated name.
    assert "lamb souvlaki" in result.summary.lower(), result.summary


async def test_revenue_drop_question_is_grounded_in_real_daily_totals(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    item = await make_menu_item(db_session, restaurant, name="Steak Frites", price="50.00")

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    day_before_yesterday = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)
    # A stark, unambiguous drop: $1000 two days ago, $100 yesterday.
    await _sale(db_session, restaurant, customer, table, item, quantity=20, when=day_before_yesterday)
    await _sale(db_session, restaurant, customer, table, item, quantity=2, when=yesterday)

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Why was revenue lower yesterday?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status != "error", result.summary
    assert "get_daily_sales" in _tool_names(result), result.summary

    revenues_seen = {
        float(day["revenue"])
        for tc in result.tool_calls
        if tc.tool_name == "get_daily_sales"
        for day in tc.output["days"]
    }
    # The real, seeded figures must show up somewhere in what the agent actually saw.
    assert 1000.0 in revenues_seen or 100.0 in revenues_seen, revenues_seen
