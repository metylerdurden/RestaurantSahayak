"""Analytics tools end-to-end against real Postgres."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import Reservation, Sale
from app.repositories.analytics_repo import AnalyticsRepository
from app.services.analytics_service import AnalyticsService
from app.tools.analytics_tools import GetDailySalesTool, GetItemSalesTool, GetNoShowRateTool
from app.tools.base import ToolContext, utcnow
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

    output = await GetNoShowRateTool(service)({"date_from": future_date, "date_to": future_date}, context=context)
    assert output.no_show_rate is None


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


async def test_get_daily_sales_period_yesterday_finds_yesterdays_real_sale(db_session):
    """The exact scenario that reproduced the original bug: a sale recorded yesterday
    must be found by period="yesterday" — deterministically, with no LLM involved."""
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    item = await make_menu_item(db_session, restaurant, name="Steak Frites", price="50.00")

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await _sale(db_session, restaurant, customer, table, item, quantity=20, when=now - timedelta(days=2))
    await _sale(db_session, restaurant, customer, table, item, quantity=2, when=now - timedelta(days=1))

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")

    output = await GetDailySalesTool(service)({"period": "yesterday"}, context=context)

    assert output.date_from == output.date_to == (now - timedelta(days=1)).date()
    assert len(output.days) == 1
    assert output.days[0].revenue == Decimal("100.00")  # 2 units * $50


async def test_get_daily_sales_period_today_only_sees_todays_sale(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    item = await make_menu_item(db_session, restaurant, name="Steak Frites", price="50.00")

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await _sale(db_session, restaurant, customer, table, item, quantity=1, when=now - timedelta(days=1))
    await _sale(db_session, restaurant, customer, table, item, quantity=3, when=now)

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")

    output = await GetDailySalesTool(service)({"period": "today"}, context=context)

    assert output.date_from == output.date_to == now.date()
    assert len(output.days) == 1
    assert output.days[0].revenue == Decimal("150.00")  # 3 units * $50, not yesterday's 1


async def test_get_daily_sales_period_last_7_days_spans_multiple_days_boundary_inclusive(db_session):
    """Boundary check: a sale exactly 6 days ago (the oldest day still inside a
    7-day rolling window) must be included; the query's half-open [start, end) bound
    at the service layer must not accidentally exclude the first or last day."""
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    item = await make_menu_item(db_session, restaurant, name="Steak Frites", price="50.00")

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await _sale(
        db_session, restaurant, customer, table, item, quantity=1, when=now - timedelta(days=6)
    )  # oldest in-window day
    await _sale(
        db_session, restaurant, customer, table, item, quantity=1, when=now - timedelta(days=7)
    )  # just outside the window
    await _sale(db_session, restaurant, customer, table, item, quantity=1, when=now)  # today, newest in-window day

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")
    output = await GetDailySalesTool(service)({"period": "last_7_days"}, context=context)

    dates_seen = {day.sales_date for day in output.days}
    assert (now - timedelta(days=6)).date() in dates_seen
    assert now.date() in dates_seen
    assert (now - timedelta(days=7)).date() not in dates_seen


async def test_get_item_sales_period_yesterday(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    winner = await make_menu_item(db_session, restaurant, name="Lamb Souvlaki", price="26.00")

    now = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await _sale(db_session, restaurant, customer, table, winner, quantity=5, when=now - timedelta(days=1))
    await _sale(db_session, restaurant, customer, table, winner, quantity=99, when=now - timedelta(days=30))

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")
    output = await GetItemSalesTool(service)({"period": "yesterday"}, context=context)

    assert len(output.items) == 1
    assert output.items[0].quantity_sold == 5  # not the 30-days-ago sale of 99


async def test_get_no_show_rate_period_this_week(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)

    now = utcnow()
    for status in ("completed", "no_show"):
        db_session.add(
            Reservation(
                restaurant_id=restaurant.id,
                customer_id=customer.id,
                table_id=table.id,
                party_size=2,
                requested_time=now,
                status=status,
                created_via="manager_request",
            )
        )
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="analytics")
    output = await GetNoShowRateTool(service)({"period": "this_week"}, context=context)

    assert output.no_show_count == 1
    assert output.completed_count == 1
    assert output.no_show_rate == pytest.approx(0.5)
    assert output.date_from.weekday() == 0  # Monday
