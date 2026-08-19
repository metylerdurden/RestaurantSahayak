"""Inventory domain business rules.

Note on scope: this phase's tool list (get_inventory, check_stock,
calculate_required_inventory, create_purchase_request) does not include a
stock-adjustment-recording tool — InventoryTransaction rows are read here (to
estimate usage) but not written by any tool in this phase. Recording deliveries/waste
is Inventory Agent territory for a later phase.

calculate_required_inventory's usage estimate: MVP has no recipe/bill-of-materials
mapping menu items to inventory items, so "usage" is estimated from recorded negative
InventoryTransaction deltas (waste/correction) over a trailing window — a documented
simplification, not a hidden guess.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from app.core.config import Settings
from app.models import InventoryItem, PurchaseRequest
from app.repositories.inventory_repo import InventoryRepository
from app.services.approval_service import ApprovalService
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError, utcnow


class InventoryService:
    def __init__(
        self, repo: InventoryRepository, approval_service: ApprovalService, settings: Settings
    ) -> None:
        self.repo = repo
        self.approval_service = approval_service
        self.settings = settings

    async def _get_item_or_raise(self, restaurant_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
        item = await self.repo.get_item(item_id)
        if item is None or item.restaurant_id != restaurant_id:
            raise ToolError("item_not_found", f"No inventory item found with id {item_id}")
        return item

    async def get_inventory(
        self, *, restaurant_id: uuid.UUID, status: str | None, name_contains: str | None
    ) -> list[InventoryItem]:
        return await self.repo.list_items(restaurant_id, status=status, name_contains=name_contains)

    async def check_stock(
        self, *, restaurant_id: uuid.UUID, item_id: uuid.UUID, required_quantity: Decimal | None
    ) -> tuple[InventoryItem, bool | None, Decimal | None]:
        item = await self._get_item_or_raise(restaurant_id, item_id)
        if required_quantity is None:
            return item, None, None
        sufficient = item.quantity_on_hand >= required_quantity
        shortfall = None if sufficient else (required_quantity - item.quantity_on_hand)
        return item, sufficient, shortfall

    async def calculate_required_inventory(
        self, *, restaurant_id: uuid.UUID, item_id: uuid.UUID, days_ahead: int
    ) -> tuple[InventoryItem, Decimal, Decimal, Decimal, int]:
        item = await self._get_item_or_raise(restaurant_id, item_id)
        lookback_days = self.settings.inventory_usage_lookback_days
        since = utcnow() - timedelta(days=lookback_days)
        transactions = await self.repo.list_transactions_since(item_id, since)

        total_consumed = sum(
            (-t.quantity_delta for t in transactions if t.quantity_delta < 0), Decimal(0)
        )
        average_daily_usage = (
            (Decimal(total_consumed) / Decimal(lookback_days)) if lookback_days > 0 else Decimal(0)
        )
        projected_quantity_at_horizon = item.quantity_on_hand - average_daily_usage * days_ahead

        # Recommend enough to bring the projected quantity back up to a 2x-threshold
        # buffer — an explicit, auditable target, not just "top up to the threshold."
        target_buffer = item.low_stock_threshold * 2
        recommended_order_quantity = max(Decimal(0), target_buffer - projected_quantity_at_horizon)

        return (
            item,
            average_daily_usage,
            projected_quantity_at_horizon,
            recommended_order_quantity,
            lookback_days,
        )

    async def create_purchase_request(
        self,
        *,
        restaurant_id: uuid.UUID,
        item_id: uuid.UUID,
        requested_quantity: Decimal,
        estimated_cost: Decimal | None,
        context: ToolContext,
    ) -> PurchaseRequest | PendingApprovalOutput:
        await self._get_item_or_raise(restaurant_id, item_id)

        high_impact = (
            estimated_cost is not None
            and estimated_cost >= self.settings.purchase_request_high_impact_cost_threshold
        )

        if high_impact:
            if context.agent_run_id is None:
                raise ToolError(
                    "agent_run_required",
                    "A high-impact purchase request must be attributed to an agent run "
                    "before it can be proposed for approval.",
                )
            purchase_request = await self.repo.create_purchase_request(
                restaurant_id=restaurant_id,
                item_id=item_id,
                requested_quantity=requested_quantity,
                estimated_cost=estimated_cost,
                status="pending_approval",
                requested_by_agent_run_id=context.agent_run_id,
            )
            approval = await self.approval_service.create_approval_request(
                restaurant_id=restaurant_id,
                domain="purchase",
                action="create_purchase_request",
                agent_name=context.acting_agent,
                proposed_by_agent_run_id=context.agent_run_id,
                parameters={
                    "action": "approve_purchase_request",
                    "purchase_request_id": str(purchase_request.id),
                },
                reason=f"Purchase {requested_quantity} units, estimated cost {estimated_cost}",
                risk_level="HIGH",
            )
            purchase_request.approval_id = approval.id
            await self.repo.save_purchase_request(purchase_request)
            return PendingApprovalOutput(approval_id=approval.id, summary=approval.reason)

        return await self.repo.create_purchase_request(
            restaurant_id=restaurant_id,
            item_id=item_id,
            requested_quantity=requested_quantity,
            estimated_cost=estimated_cost,
            status="approved",
            requested_by_agent_run_id=context.agent_run_id,
        )

    async def execute_approved_action(self, approval) -> PurchaseRequest:
        """Applies an approved purchase request. Invoked automatically by
        ApprovalService.approve() when wired with an executor (see
        app.services.approval_execution.build_executors); still directly callable
        for a caller that only wants ApprovalService's plain decision state machine."""
        action = approval.parameters["action"]
        if action != "approve_purchase_request":
            raise ToolError("unsupported_action", f"Cannot execute approved action: {action}")
        purchase_request_id = uuid.UUID(approval.parameters["purchase_request_id"])
        purchase_request = await self.repo.get_purchase_request(purchase_request_id)
        if purchase_request is None:
            raise ToolError(
                "purchase_request_not_found", f"No purchase request found with id {purchase_request_id}"
            )
        purchase_request.status = "approved"
        return await self.repo.save_purchase_request(purchase_request)
