"""Analytics domain — read-only, no mutating capability by design (Constitution
consistent: Analytics answers questions over existing structured data, never writes).

Date ranges are treated as whole UTC calendar days [date_from 00:00, date_to+1 00:00) —
an MVP simplification; restaurant-local-timezone-aware reporting is a natural
follow-up once that becomes a real requirement.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.repositories.analytics_repo import AnalyticsRepository
from app.schemas.analytics import DailySalesEntry, ItemSalesEntry


def _day_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


class AnalyticsService:
    def __init__(self, repo: AnalyticsRepository) -> None:
        self.repo = repo

    async def get_daily_sales(
        self, *, restaurant_id: uuid.UUID, date_from: date, date_to: date
    ) -> list[DailySalesEntry]:
        start, end = _day_bounds(date_from, date_to)
        sales_rows = await self.repo.daily_sales(restaurant_id, start, end)
        covers_by_date = await self.repo.covers_by_date(restaurant_id, start, end)

        return [
            DailySalesEntry(
                sales_date=row.sales_date,
                revenue=row.revenue,
                items_sold=row.items_sold,
                covers=covers_by_date.get(row.sales_date, 0),
            )
            for row in sales_rows
        ]

    async def get_item_sales(
        self, *, restaurant_id: uuid.UUID, date_from: date, date_to: date, limit: int
    ) -> list[ItemSalesEntry]:
        start, end = _day_bounds(date_from, date_to)
        rows = await self.repo.item_sales(restaurant_id, start, end, limit)
        return [
            ItemSalesEntry(menu_item_id=row[0], name=row[1], quantity_sold=row[2], revenue=row[3])
            for row in rows
        ]

    async def get_no_show_rate(
        self, *, restaurant_id: uuid.UUID, date_from: date, date_to: date
    ) -> tuple[int, int, Decimal | None]:
        start, end = _day_bounds(date_from, date_to)
        stats = await self.repo.no_show_stats(restaurant_id, start, end)
        no_show_count = stats.get("no_show", 0)
        completed_count = stats.get("completed", 0)
        denominator = no_show_count + completed_count
        rate = (
            (Decimal(no_show_count) / Decimal(denominator)).quantize(Decimal("0.0001"))
            if denominator > 0
            else None
        )
        return no_show_count, completed_count, rate
