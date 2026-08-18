from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InventoryItemDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    unit: str
    quantity_on_hand: Decimal
    low_stock_threshold: Decimal
    status: str


class PurchaseRequestDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    requested_quantity: Decimal
    estimated_cost: Decimal | None
    status: str
    approval_id: uuid.UUID | None


# --- get_inventory ---


class GetInventoryInput(BaseModel):
    status: str | None = None
    name_contains: str | None = None


class GetInventoryOutput(BaseModel):
    items: list[InventoryItemDTO]


# --- check_stock ---


class CheckStockInput(BaseModel):
    item_id: uuid.UUID
    required_quantity: Decimal | None = Field(default=None, ge=0)


class CheckStockOutput(BaseModel):
    item: InventoryItemDTO
    sufficient: bool | None
    shortfall: Decimal | None


# --- calculate_required_inventory ---


class CalculateRequiredInventoryInput(BaseModel):
    item_id: uuid.UUID
    days_ahead: int = Field(default=7, gt=0, le=90)


class CalculateRequiredInventoryOutput(BaseModel):
    item: InventoryItemDTO
    average_daily_usage: Decimal
    projected_quantity_at_horizon: Decimal
    recommended_order_quantity: Decimal
    lookback_days_used: int


# --- create_purchase_request ---


class CreatePurchaseRequestInput(BaseModel):
    item_id: uuid.UUID
    requested_quantity: Decimal = Field(gt=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)


class CreatePurchaseRequestOutput(BaseModel):
    purchase_request: PurchaseRequestDTO
