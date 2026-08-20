"""Analytics tools — typed, read-only surface over AnalyticsService. No mutating
tool exists in this domain by design."""

from __future__ import annotations

from app.schemas.analytics import (
    GetDailySalesInput,
    GetDailySalesOutput,
    GetItemSalesInput,
    GetItemSalesOutput,
    GetNoShowRateInput,
    GetNoShowRateOutput,
)
from app.services.analytics_service import AnalyticsService
from app.tools.base import Tool, ToolContext


class GetDailySalesTool(Tool[GetDailySalesInput, GetDailySalesOutput]):
    name = "get_daily_sales"
    description = "Get revenue, items sold, and covers per day over a date range."
    input_model = GetDailySalesInput
    output_model = GetDailySalesOutput

    def __init__(self, service: AnalyticsService) -> None:
        self.service = service

    async def run(self, input: GetDailySalesInput, *, context: ToolContext) -> GetDailySalesOutput:
        days = await self.service.get_daily_sales(
            restaurant_id=context.restaurant_id, date_from=input.date_from, date_to=input.date_to
        )
        return GetDailySalesOutput(days=days)


class GetItemSalesTool(Tool[GetItemSalesInput, GetItemSalesOutput]):
    name = "get_item_sales"
    description = "Get the best-selling menu items (by quantity) over a date range."
    input_model = GetItemSalesInput
    output_model = GetItemSalesOutput

    def __init__(self, service: AnalyticsService) -> None:
        self.service = service

    async def run(self, input: GetItemSalesInput, *, context: ToolContext) -> GetItemSalesOutput:
        items = await self.service.get_item_sales(
            restaurant_id=context.restaurant_id,
            date_from=input.date_from,
            date_to=input.date_to,
            limit=input.limit,
        )
        return GetItemSalesOutput(items=items)


class GetNoShowRateTool(Tool[GetNoShowRateInput, GetNoShowRateOutput]):
    name = "get_no_show_rate"
    description = "Get the no-show rate (no-shows as a fraction of no-shows + completed visits) over a date range."
    input_model = GetNoShowRateInput
    output_model = GetNoShowRateOutput

    def __init__(self, service: AnalyticsService) -> None:
        self.service = service

    async def run(self, input: GetNoShowRateInput, *, context: ToolContext) -> GetNoShowRateOutput:
        no_show_count, completed_count, rate = await self.service.get_no_show_rate(
            restaurant_id=context.restaurant_id, date_from=input.date_from, date_to=input.date_to
        )
        return GetNoShowRateOutput(
            date_from=input.date_from,
            date_to=input.date_to,
            no_show_count=no_show_count,
            completed_count=completed_count,
            no_show_rate=rate,
        )
