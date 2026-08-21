from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# A small, closed set of unambiguous relative periods a caller can name instead of
# computing an absolute date_from/date_to itself — see
# app.tools.analytics_tools.resolve_period for the deterministic resolution. Kept
# intentionally bounded (not free-form date-math parsing): anything outside this set
# still works via explicit date_from/date_to.
AnalyticsPeriod = Literal[
    "today", "yesterday", "this_week", "last_week", "last_7_days", "last_30_days", "this_month", "last_month"
]


class _PeriodOrExplicitDatesInput(BaseModel):
    """Shared validation for every analytics tool input: provide exactly one of a
    named `period` or an explicit `date_from`/`date_to` pair — never both, never
    neither, and never a reversed range."""

    period: AnalyticsPeriod | None = None
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _check_period_xor_explicit_dates(self) -> "_PeriodOrExplicitDatesInput":
        has_period = self.period is not None
        has_explicit_dates = self.date_from is not None or self.date_to is not None
        if has_period and has_explicit_dates:
            raise ValueError("Provide either `period` or `date_from`/`date_to`, not both.")
        if not has_period and not has_explicit_dates:
            raise ValueError("Provide either `period` or both `date_from` and `date_to`.")
        if has_explicit_dates and (self.date_from is None or self.date_to is None):
            raise ValueError("Both `date_from` and `date_to` are required when not using `period`.")
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("`date_from` must not be after `date_to`.")
        return self


# --- get_daily_sales ---


class GetDailySalesInput(_PeriodOrExplicitDatesInput):
    pass


class DailySalesEntry(BaseModel):
    sales_date: date
    revenue: Decimal
    items_sold: int
    covers: int


class GetDailySalesOutput(BaseModel):
    date_from: date
    date_to: date
    days: list[DailySalesEntry]


# --- get_item_sales ---


class GetItemSalesInput(_PeriodOrExplicitDatesInput):
    limit: int = Field(default=10, gt=0, le=100)


class ItemSalesEntry(BaseModel):
    menu_item_id: uuid.UUID
    name: str
    quantity_sold: int
    revenue: Decimal


class GetItemSalesOutput(BaseModel):
    date_from: date
    date_to: date
    items: list[ItemSalesEntry]


# --- get_no_show_rate ---


class GetNoShowRateInput(_PeriodOrExplicitDatesInput):
    pass


class GetNoShowRateOutput(BaseModel):
    date_from: date
    date_to: date
    no_show_count: int
    completed_count: int
    no_show_rate: Decimal | None  # None when there's no completed/no-show data to compute from
