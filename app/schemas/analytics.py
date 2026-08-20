from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

# --- get_daily_sales ---


class GetDailySalesInput(BaseModel):
    date_from: date
    date_to: date


class DailySalesEntry(BaseModel):
    sales_date: date
    revenue: Decimal
    items_sold: int
    covers: int


class GetDailySalesOutput(BaseModel):
    days: list[DailySalesEntry]


# --- get_item_sales ---


class GetItemSalesInput(BaseModel):
    date_from: date
    date_to: date
    limit: int = Field(default=10, gt=0, le=100)


class ItemSalesEntry(BaseModel):
    menu_item_id: uuid.UUID
    name: str
    quantity_sold: int
    revenue: Decimal


class GetItemSalesOutput(BaseModel):
    items: list[ItemSalesEntry]


# --- get_no_show_rate ---


class GetNoShowRateInput(BaseModel):
    date_from: date
    date_to: date


class GetNoShowRateOutput(BaseModel):
    date_from: date
    date_to: date
    no_show_count: int
    completed_count: int
    no_show_rate: Decimal | None  # None when there's no completed/no-show data to compute from
