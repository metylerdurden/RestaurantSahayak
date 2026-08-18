"""AnalyticsService business rules against mocked repository — no database. Focused
on the no-show-rate formula (including the "insufficient data -> None" rule, matching
FR-018's "say so rather than fabricate a figure")."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.repositories.analytics_repo import AnalyticsRepository
from app.services.analytics_service import AnalyticsService

RESTAURANT_ID = uuid.uuid4()


@pytest.mark.asyncio
async def test_no_show_rate_computed_correctly():
    repo = AsyncMock(spec=AnalyticsRepository)
    repo.no_show_stats.return_value = {"no_show": 3, "completed": 17}
    service = AnalyticsService(repo)

    no_show, completed, rate = await service.get_no_show_rate(
        restaurant_id=RESTAURANT_ID, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)
    )
    assert no_show == 3
    assert completed == 17
    assert rate == Decimal("3") / Decimal("20")


@pytest.mark.asyncio
async def test_no_show_rate_is_none_when_no_data():
    repo = AsyncMock(spec=AnalyticsRepository)
    repo.no_show_stats.return_value = {}
    service = AnalyticsService(repo)

    no_show, completed, rate = await service.get_no_show_rate(
        restaurant_id=RESTAURANT_ID, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)
    )
    assert no_show == 0
    assert completed == 0
    assert rate is None


@pytest.mark.asyncio
async def test_get_daily_sales_merges_covers_by_date():
    repo = AsyncMock(spec=AnalyticsRepository)
    row = type("Row", (), {"sales_date": date(2026, 1, 5), "revenue": Decimal("120.00"), "items_sold": 8})()
    repo.daily_sales.return_value = [row]
    repo.covers_by_date.return_value = {date(2026, 1, 5): 12}
    service = AnalyticsService(repo)

    days = await service.get_daily_sales(
        restaurant_id=RESTAURANT_ID, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)
    )
    assert days[0].covers == 12
    assert days[0].revenue == Decimal("120.00")


@pytest.mark.asyncio
async def test_get_daily_sales_defaults_covers_to_zero_when_missing():
    repo = AsyncMock(spec=AnalyticsRepository)
    row = type("Row", (), {"sales_date": date(2026, 1, 5), "revenue": Decimal("50.00"), "items_sold": 3})()
    repo.daily_sales.return_value = [row]
    repo.covers_by_date.return_value = {}
    service = AnalyticsService(repo)

    days = await service.get_daily_sales(
        restaurant_id=RESTAURANT_ID, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)
    )
    assert days[0].covers == 0
