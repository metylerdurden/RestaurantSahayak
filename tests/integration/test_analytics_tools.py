"""Analytics tools end-to-end against real Postgres."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models import Reservation, Sale
from app.repositories.analytics_repo import AnalyticsRepository
from app.services.analytics_service import AnalyticsService
from app.tools.base import ToolContext, utcnow
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool
from tests.integration.factories import make_customer, make_menu_item, make_restaurant, make_table

pytestmark = pytest.mark.asyncio


async def _build(db_session):
    return AnalyticsService(AnalyticsRepository(db_session))


def _today() -> date:
    return utcnow().date()


async def test_get_daily_sales_and_item_sales_end_to_end(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    baklava = await make_menu_item(db_session, restaurant, name="Baklava", price="8.00")
    lamb = await make_menu_item(db_session, restaurant, name="Lamb Souvlaki", price="26.00")

    reservation = Reservation(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        table_id=table.id,
        party_size=4,
        requested_time=utcnow(),
        status="completed",
        created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()

    db_session.add_all(
        [
            Sale(
                restaurant_id=restaurant.id,
                reservation_id=reservation.id,
                menu_item_id=lamb.id,
                quantity=2,
                unit_price=lamb.price,
                sold_at=utcnow(),
            ),
            Sale(
                restaurant_id=restaurant.id,
                reservation_id=reservation.id,
                menu_item_id=baklava.id,
                quantity=4,
                unit_price=baklava.price,
                sold_at=utcnow(),
            ),
        ]
    )
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")
    today = _today()

    sales_output = await GetDailySalesTool(service)(
        {"date_from": today.isoformat(), "date_to": today.isoformat()}, context=context
    )
    assert len(sales_output.days) == 1
    assert sales_output.days[0].covers == 4
    assert sales_output.days[0].items_sold == 6
    assert sales_output.days[0].revenue == 2 * 26 + 4 * 8

    item_output = await GetItemSalesTool(service)(
        {"date_from": today.isoformat(), "date_to": today.isoformat(), "limit": 5}, context=context
    )
    assert item_output.items[0].name == "Baklava"  # higher quantity sold (4 > 2)


async def test_get_no_show_rate_end_to_end(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)

    for status in ("completed", "completed", "completed", "no_show"):
        db_session.add(
            Reservation(
                restaurant_id=restaurant.id,
                customer_id=customer.id,
                table_id=table.id,
                party_size=2,
                requested_time=utcnow(),
                status=status,
                created_via="manager_request",
            )
        )
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")
    today = _today()
    output = await GetNoShowRateTool(service)(
        {"date_from": today.isoformat(), "date_to": today.isoformat()}, context=context
    )
    assert output.no_show_count == 1
    assert output.completed_count == 3
    assert output.no_show_rate == pytest.approx(0.25)


async def test_get_no_show_rate_returns_none_with_no_data(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")
    future_date = (_today() + timedelta(days=365)).isoformat()

    output = await GetNoShowRateTool(service)(
        {"date_from": future_date, "date_to": future_date}, context=context
    )
    assert output.no_show_rate is None
