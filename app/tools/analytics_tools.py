"""Analytics tools — typed, read-only surface over AnalyticsService. No mutating
tool exists in this domain by design.

Date-range resolution: every tool here accepts either a named `period` (see
AnalyticsPeriod) or an explicit `date_from`/`date_to` pair — never leaves the LLM to
compute an absolute date from a relative phrase itself. resolve_period() below is the
one deterministic place that turns "yesterday" into a real date; AnalyticsService and
AnalyticsRepository are unchanged and still only ever see concrete dates.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.schemas.analytics import (
    AnalyticsPeriod,
    GetDailySalesInput,
    GetDailySalesOutput,
    GetItemSalesInput,
    GetItemSalesOutput,
    GetNoShowRateInput,
    GetNoShowRateOutput,
)
from app.services.analytics_service import AnalyticsService
from app.tools.base import Tool, ToolContext, utcnow


def resolve_period(period: AnalyticsPeriod, *, today: date) -> tuple[date, date]:
    """Deterministic date_from/date_to for a fixed, unambiguous set of relative
    periods, resolved from `today` — never from the LLM's own arithmetic. This is
    the fix for a reproduced bug where the model computed wildly wrong absolute
    dates for phrases like "yesterday" (see tests/unit/test_analytics_date_ranges.py
    for the exact regression)."""
    if period == "today":
        return today, today
    if period == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if period == "this_week":
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=6)
    if period == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        return last_monday, last_monday + timedelta(days=6)
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "last_30_days":
        return today - timedelta(days=29), today
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first_of_this_month = today.replace(day=1)
        last_of_last_month = first_of_this_month - timedelta(days=1)
        return last_of_last_month.replace(day=1), last_of_last_month
    # Unreachable given AnalyticsPeriod's Literal type (Pydantic validates the input
    # before this function is ever called) — defensive only, never invents a range.
    raise ValueError(f"Unknown period: {period!r}")


def _resolve_dates(input: GetDailySalesInput | GetItemSalesInput | GetNoShowRateInput) -> tuple[date, date]:
    if input.period is not None:
        return resolve_period(input.period, today=utcnow().date())
    assert input.date_from is not None and input.date_to is not None  # enforced by the input model's validator
    return input.date_from, input.date_to


class GetDailySalesTool(Tool[GetDailySalesInput, GetDailySalesOutput]):
    name = "get_daily_sales"
    description = (
        "Get revenue, items sold, and covers per day over a date range. Prefer "
        "period (today/yesterday/this_week/last_week/last_7_days/last_30_days/"
        "this_month/last_month) over computing date_from/date_to yourself — only use "
        "explicit date_from/date_to for a specific date or range period doesn't cover."
    )
    input_model = GetDailySalesInput
    output_model = GetDailySalesOutput

    def __init__(self, service: AnalyticsService) -> None:
        self.service = service

    async def run(self, input: GetDailySalesInput, *, context: ToolContext) -> GetDailySalesOutput:
        date_from, date_to = _resolve_dates(input)
        days = await self.service.get_daily_sales(
            restaurant_id=context.restaurant_id, date_from=date_from, date_to=date_to
        )
        return GetDailySalesOutput(date_from=date_from, date_to=date_to, days=days)


class GetItemSalesTool(Tool[GetItemSalesInput, GetItemSalesOutput]):
    name = "get_item_sales"
    description = (
        "Get the best-selling menu items (by quantity) over a date range. Prefer "
        "period (today/yesterday/this_week/last_week/last_7_days/last_30_days/"
        "this_month/last_month) over computing date_from/date_to yourself — only use "
        "explicit date_from/date_to for a specific date or range period doesn't cover."
    )
    input_model = GetItemSalesInput
    output_model = GetItemSalesOutput

    def __init__(self, service: AnalyticsService) -> None:
        self.service = service

    async def run(self, input: GetItemSalesInput, *, context: ToolContext) -> GetItemSalesOutput:
        date_from, date_to = _resolve_dates(input)
        items = await self.service.get_item_sales(
            restaurant_id=context.restaurant_id, date_from=date_from, date_to=date_to, limit=input.limit
        )
        return GetItemSalesOutput(date_from=date_from, date_to=date_to, items=items)


class GetNoShowRateTool(Tool[GetNoShowRateInput, GetNoShowRateOutput]):
    name = "get_no_show_rate"
    description = (
        "Get the no-show rate (no-shows as a fraction of no-shows + completed visits) "
        "over a date range. Prefer period (today/yesterday/this_week/last_week/"
        "last_7_days/last_30_days/this_month/last_month) over computing "
        "date_from/date_to yourself — only use explicit date_from/date_to for a "
        "specific date or range period doesn't cover."
    )
    input_model = GetNoShowRateInput
    output_model = GetNoShowRateOutput

    def __init__(self, service: AnalyticsService) -> None:
        self.service = service

    async def run(self, input: GetNoShowRateInput, *, context: ToolContext) -> GetNoShowRateOutput:
        date_from, date_to = _resolve_dates(input)
        no_show_count, completed_count, rate = await self.service.get_no_show_rate(
            restaurant_id=context.restaurant_id, date_from=date_from, date_to=date_to
        )
        return GetNoShowRateOutput(
            date_from=date_from,
            date_to=date_to,
            no_show_count=no_show_count,
            completed_count=completed_count,
            no_show_rate=rate,
        )
