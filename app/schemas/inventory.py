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


# --- analyze_inventory ---


class InventoryAlertDTO(BaseModel):
    """One out-of-stock or low item plus its deterministic severity and (when
    computable) a reorder recommendation. Composes InventoryItemDTO rather than
    repeating its fields."""

    item: InventoryItemDTO
    status: str
    severity: str
    action_required: bool
    recommended_reorder_quantity: Decimal | None
    reorder_quantity_available: bool
    reason: str | None = None


class AnalyzeInventoryInput(BaseModel):
    days_ahead: int = Field(default=7, gt=0, le=90)


class AnalyzeInventoryOutput(BaseModel):
    alerts: list[InventoryAlertDTO]
    critical_count: int
    warning_count: int
    action_required: bool


# --- calculate_reorder_quantity ---


class CalculateReorderQuantityInput(BaseModel):
    item_id: uuid.UUID


class CalculateReorderQuantityOutput(BaseModel):
    """Deterministic, non-projected reorder sizing for one item — target_quantity
    minus current_quantity, floored at zero. See
    InventoryService.calculate_reorder_quantity / ._target_quantity for the rule."""

    item_id: uuid.UUID
    item_name: str
    current_quantity: Decimal
    threshold: Decimal
    target_quantity: Decimal
    recommended_order_quantity: Decimal
    unit: str


# --- structured run summary (Inventory Agent only; derived from AgentResult.tool_calls) ---


class InventoryRunSummary(BaseModel):
    """Distinguishes what the Inventory Agent observed from what it recommended,
    what it actually did (auto-approved actions), and what still needs a manager's
    decision — derived entirely from the run's own recorded tool calls, never from
    the model's prose. See app.agents.inventory_agent.summarize_inventory_run."""

    observations: list[dict] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)
    actions_taken: list[dict] = Field(default_factory=list)
    pending_approvals: list[dict] = Field(default_factory=list)
