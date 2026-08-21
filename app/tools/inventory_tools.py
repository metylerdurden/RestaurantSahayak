"""Inventory tools — typed surface over InventoryService."""

from __future__ import annotations

from app.schemas.inventory import (
    AnalyzeInventoryInput,
    AnalyzeInventoryOutput,
    CalculateRequiredInventoryInput,
    CalculateRequiredInventoryOutput,
    CheckStockInput,
    CheckStockOutput,
    CreatePurchaseRequestInput,
    CreatePurchaseRequestOutput,
    GetInventoryInput,
    GetInventoryOutput,
    InventoryAlertDTO,
    InventoryItemDTO,
    PurchaseRequestDTO,
)
from app.services.inventory_service import InventoryService
from app.tools.base import PendingApprovalOutput, Tool, ToolContext


class GetInventoryTool(Tool[GetInventoryInput, GetInventoryOutput]):
    name = "get_inventory"
    description = "List inventory items, optionally filtered by status (ok/low/out_of_stock) or name."
    input_model = GetInventoryInput
    output_model = GetInventoryOutput

    def __init__(self, service: InventoryService) -> None:
        self.service = service

    async def run(self, input: GetInventoryInput, *, context: ToolContext) -> GetInventoryOutput:
        items = await self.service.get_inventory(
            restaurant_id=context.restaurant_id, status=input.status, name_contains=input.name_contains
        )
        return GetInventoryOutput(items=[InventoryItemDTO.model_validate(i) for i in items])


class CheckStockTool(Tool[CheckStockInput, CheckStockOutput]):
    name = "check_stock"
    description = (
        "Check a specific inventory item's current stock level, optionally against a "
        "required quantity to see if there's enough on hand."
    )
    input_model = CheckStockInput
    output_model = CheckStockOutput

    def __init__(self, service: InventoryService) -> None:
        self.service = service

    async def run(self, input: CheckStockInput, *, context: ToolContext) -> CheckStockOutput:
        item, sufficient, shortfall = await self.service.check_stock(
            restaurant_id=context.restaurant_id,
            item_id=input.item_id,
            required_quantity=input.required_quantity,
            context=context,
        )
        return CheckStockOutput(item=InventoryItemDTO.model_validate(item), sufficient=sufficient, shortfall=shortfall)


class CalculateRequiredInventoryTool(Tool[CalculateRequiredInventoryInput, CalculateRequiredInventoryOutput]):
    name = "calculate_required_inventory"
    description = (
        "Estimate how much of an inventory item should be ordered to stay stocked "
        "over the next N days, based on recent usage."
    )
    input_model = CalculateRequiredInventoryInput
    output_model = CalculateRequiredInventoryOutput

    def __init__(self, service: InventoryService) -> None:
        self.service = service

    async def run(
        self, input: CalculateRequiredInventoryInput, *, context: ToolContext
    ) -> CalculateRequiredInventoryOutput:
        item, avg_usage, projected, recommended, lookback_days = await self.service.calculate_required_inventory(
            restaurant_id=context.restaurant_id, item_id=input.item_id, days_ahead=input.days_ahead
        )
        return CalculateRequiredInventoryOutput(
            item=InventoryItemDTO.model_validate(item),
            average_daily_usage=avg_usage,
            projected_quantity_at_horizon=projected,
            recommended_order_quantity=recommended,
            lookback_days_used=lookback_days,
        )


class AnalyzeInventoryTool(Tool[AnalyzeInventoryInput, AnalyzeInventoryOutput]):
    name = "analyze_inventory"
    description = (
        "Get every inventory item that is currently low or out of stock, in one call, "
        "already classified by severity (critical/warning) with a recommended reorder "
        "quantity computed from actual usage where possible. Use this instead of calling "
        "get_inventory multiple times with different status filters."
    )
    input_model = AnalyzeInventoryInput
    output_model = AnalyzeInventoryOutput

    def __init__(self, service: InventoryService) -> None:
        self.service = service

    async def run(self, input: AnalyzeInventoryInput, *, context: ToolContext) -> AnalyzeInventoryOutput:
        alerts, critical_count, warning_count, action_required = await self.service.analyze_inventory(
            restaurant_id=context.restaurant_id, days_ahead=input.days_ahead
        )
        return AnalyzeInventoryOutput(
            alerts=[
                InventoryAlertDTO(
                    item=InventoryItemDTO.model_validate(a.item),
                    status=a.status,
                    severity=a.severity,
                    action_required=a.action_required,
                    recommended_reorder_quantity=a.recommended_reorder_quantity,
                    reorder_quantity_available=a.reorder_quantity_available,
                    reason=a.reason,
                )
                for a in alerts
            ],
            critical_count=critical_count,
            warning_count=warning_count,
            action_required=action_required,
        )


class CreatePurchaseRequestTool(Tool[CreatePurchaseRequestInput, CreatePurchaseRequestOutput]):
    name = "create_purchase_request"
    description = (
        "Create a request to restock an inventory item. High-cost requests require "
        "manager approval before they take effect."
    )
    input_model = CreatePurchaseRequestInput
    output_model = CreatePurchaseRequestOutput
    high_impact = True  # conditional on estimated cost — see InventoryService

    def __init__(self, service: InventoryService) -> None:
        self.service = service

    async def run(
        self, input: CreatePurchaseRequestInput, *, context: ToolContext
    ) -> CreatePurchaseRequestOutput | PendingApprovalOutput:
        result = await self.service.create_purchase_request(
            restaurant_id=context.restaurant_id,
            item_id=input.item_id,
            requested_quantity=input.requested_quantity,
            estimated_cost=input.estimated_cost,
            context=context,
        )
        if isinstance(result, PendingApprovalOutput):
            return result
        return CreatePurchaseRequestOutput(purchase_request=PurchaseRequestDTO.model_validate(result))
