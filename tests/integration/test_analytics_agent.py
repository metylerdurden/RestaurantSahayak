"""AnalyticsAgent against real Postgres data (real AnalyticsService/tools, real
queries) with a scripted LLMProvider — no real model call, so this is fast and
deterministic while still proving the agent correctly wires into real aggregate
queries end to end. Real Qwen3-8B reasoning is exercised separately in
tests/integration/test_analytics_agent_live.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.analytics_agent import AnalyticsAgent
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall
from app.models import Reservation, Sale
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.analytics_repo import AnalyticsRepository
from app.services.agent_run_service import AgentRunService
from app.services.analytics_service import AnalyticsService
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool
from app.tools.base import utcnow
from tests.integration.factories import make_customer, make_menu_item, make_restaurant, make_table, make_user

pytestmark = pytest.mark.asyncio


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)

    async def health_check(self) -> bool:
        return True


def _build_agent(db_session, llm) -> AnalyticsAgent:
    service = AnalyticsService(AnalyticsRepository(db_session))
    agent_run_service = AgentRunService(AgentRunRepository(db_session))
    tools = [GetDailySalesTool(service), GetItemSalesTool(service), GetNoShowRateTool(service)]
    return AnalyticsAgent(llm=llm, tools=tools, agent_run_service=agent_run_service)


def _today() -> date:
    return utcnow().date()


async def test_item_sales_question_reflects_real_sales_data(db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    lamb = await make_menu_item(db_session, restaurant, name="Lamb Souvlaki", price="26.00")
    baklava = await make_menu_item(db_session, restaurant, name="Baklava", price="8.00")

    reservation = Reservation(
        restaurant_id=restaurant.id, customer_id=customer.id, table_id=table.id, party_size=4,
        requested_time=utcnow(), status="completed", created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()
    db_session.add_all(
        [
            Sale(restaurant_id=restaurant.id, reservation_id=reservation.id, menu_item_id=lamb.id, quantity=6, unit_price=lamb.price, sold_at=utcnow()),
            Sale(restaurant_id=restaurant.id, reservation_id=reservation.id, menu_item_id=baklava.id, quantity=2, unit_price=baklava.price, sold_at=utcnow()),
        ]
    )
    await db_session.flush()

    today = _today().isoformat()
    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_item_sales", arguments={"date_from": today, "date_to": today, "limit": 5})],
        ),
        LLMResponse(content="Lamb Souvlaki was the best seller today.", model="fake-model"),
    ]
    agent = _build_agent(db_session, ScriptedLLM(responses))

    result = await agent.handle(
        "Which menu items performed best today?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status == "completed"
    items = result.tool_calls[0].output["items"]
    assert items[0]["name"] == "Lamb Souvlaki"
    assert items[0]["quantity_sold"] == 6


async def test_revenue_comparison_question_reflects_two_real_days(db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    item = await make_menu_item(db_session, restaurant, name="Steak", price="30.00")

    today = _today()
    yesterday = today - timedelta(days=1)
    for day, quantity in [(today, 2), (yesterday, 10)]:
        reservation = Reservation(
            restaurant_id=restaurant.id, customer_id=customer.id, table_id=table.id, party_size=2,
            requested_time=utcnow().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=(today - day).days),
            status="completed", created_via="manager_request",
        )
        db_session.add(reservation)
        await db_session.flush()
        db_session.add(
            Sale(
                restaurant_id=restaurant.id, reservation_id=reservation.id, menu_item_id=item.id,
                quantity=quantity, unit_price=item.price,
                sold_at=utcnow().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=(today - day).days),
            )
        )
    await db_session.flush()

    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_daily_sales", arguments={"date_from": today.isoformat(), "date_to": today.isoformat()})],
        ),
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c1", name="get_daily_sales", arguments={"date_from": yesterday.isoformat(), "date_to": yesterday.isoformat()})],
        ),
        LLMResponse(content="Revenue today ($60.00) is well below yesterday's ($300.00).", model="fake-model"),
    ]
    agent = _build_agent(db_session, ScriptedLLM(responses))

    result = await agent.handle(
        "Why was revenue lower today than yesterday?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status == "completed"
    today_revenue = result.tool_calls[0].output["days"][0]["revenue"]
    yesterday_revenue = result.tool_calls[1].output["days"][0]["revenue"]
    assert float(today_revenue) == 60.0
    assert float(yesterday_revenue) == 300.0


async def test_no_show_rate_question_reflects_real_reservation_outcomes(db_session):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)

    for status in ("completed", "completed", "completed", "no_show"):
        db_session.add(
            Reservation(
                restaurant_id=restaurant.id, customer_id=customer.id, table_id=table.id, party_size=2,
                requested_time=utcnow(), status=status, created_via="manager_request",
            )
        )
    await db_session.flush()

    today = _today().isoformat()
    responses = [
        LLMResponse(
            content="", model="fake-model",
            tool_calls=[ToolCall(id="c0", name="get_no_show_rate", arguments={"date_from": today, "date_to": today})],
        ),
        LLMResponse(content="The no-show rate today is 25% (1 of 4).", model="fake-model"),
    ]
    agent = _build_agent(db_session, ScriptedLLM(responses))

    result = await agent.handle(
        "What's today's no-show rate?", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status == "completed"
    output = result.tool_calls[0].output
    assert output["no_show_count"] == 1
    assert output["completed_count"] == 3
    assert float(output["no_show_rate"]) == pytest.approx(0.25)
