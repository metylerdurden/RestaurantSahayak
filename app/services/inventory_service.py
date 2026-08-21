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

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.core.config import Settings
from app.models import InventoryItem, PurchaseRequest
from app.repositories.inventory_repo import InventoryRepository
from app.services.approval_service import ApprovalService
from app.services.event_bus import EventBus
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError, utcnow

# Deterministic status/severity rules (Constitution: the application, not the LLM,
# owns business-critical numbers and classifications). Matches the exact formula
# already used independently by scripts/seed.py and tests/integration/factories.py —
# centralized here so analyze_inventory/list_alerts, the dashboard, and the
# /inventory/alerts route all agree with each other by construction.
_SEVERITY_BY_STATUS = {"out_of_stock": "critical", "low": "warning", "ok": "none"}


@dataclass(frozen=True)
class InventoryAlert:
    """One flagged item plus its deterministic severity and (when computable) a
    reorder recommendation — the structured unit both AnalyzeInventoryTool and any
    future non-agent caller build their output from. Never constructed with a
    hallucinated number: recommended_reorder_quantity is either a real result of
    calculate_required_inventory or None with reorder_quantity_available=False and a
    concrete reason."""

    item: InventoryItem
    status: str
    severity: str
    action_required: bool
    recommended_reorder_quantity: Decimal | None
    reorder_quantity_available: bool
    reason: str | None


class InventoryService:
    def __init__(
        self,
        repo: InventoryRepository,
        approval_service: ApprovalService,
        settings: Settings,
        event_bus: EventBus | None = None,
    ) -> None:
        self.repo = repo
        self.approval_service = approval_service
        self.settings = settings
        self.event_bus = event_bus

    async def _publish(
        self,
        event_type: str,
        *,
        restaurant_id: uuid.UUID,
        entity_id: uuid.UUID,
        payload: dict[str, Any],
        correlation_id: uuid.UUID | None,
    ) -> None:
        if self.event_bus is None:
            return
        await self.event_bus.publish(
            event_type=event_type,
            restaurant_id=restaurant_id,
            entity_id=entity_id,
            payload=payload,
            correlation_id=correlation_id,
            published_by="inventory_service",
        )

    async def _get_item_or_raise(self, restaurant_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
        item = await self.repo.get_item(item_id)
        if item is None or item.restaurant_id != restaurant_id:
            raise ToolError("item_not_found", f"No inventory item found with id {item_id}")
        return item

    async def get_inventory(
        self, *, restaurant_id: uuid.UUID, status: str | None, name_contains: str | None
    ) -> list[InventoryItem]:
        return await self.repo.list_items(restaurant_id, status=status, name_contains=name_contains)

    def _classify(self, item: InventoryItem) -> tuple[str, str, bool]:
        """status, severity, action_required — recomputed from quantity_on_hand vs
        low_stock_threshold rather than trusted from item.status: nothing in this
        service currently keeps that stored column in sync when quantity changes, so
        the numeric fields (enforced non-negative by a DB constraint) are the only
        value actually safe to treat as the source of truth."""
        if item.quantity_on_hand <= 0:
            status = "out_of_stock"
        elif item.quantity_on_hand <= item.low_stock_threshold:
            status = "low"
        else:
            status = "ok"
        return status, _SEVERITY_BY_STATUS[status], status != "ok"

    async def list_alerts(self, *, restaurant_id: uuid.UUID) -> list[InventoryItem]:
        """Every item currently out of stock or low, fetched with a single query —
        the shared source of truth for the Inventory Agent's analysis, the dashboard's
        inventory_alerts panel, and GET /inventory/alerts, replacing what used to be
        three independent copies of a two-call (status="out_of_stock" then
        status="low") pattern."""
        items = await self.repo.list_items(restaurant_id, status=None, name_contains=None)
        flagged = [i for i in items if self._classify(i)[2]]
        flagged.sort(key=lambda i: (self._classify(i)[0] != "out_of_stock", i.name))
        return flagged

    async def _build_alert(self, item: InventoryItem, *, days_ahead: int) -> InventoryAlert:
        status, severity, action_required = self._classify(item)
        try:
            _, _, _, recommended, _ = await self.calculate_required_inventory(
                restaurant_id=item.restaurant_id, item_id=item.id, days_ahead=days_ahead
            )
            return InventoryAlert(item, status, severity, action_required, recommended, True, None)
        except ToolError as exc:
            return InventoryAlert(item, status, severity, action_required, None, False, exc.message)

    async def analyze_inventory(
        self, *, restaurant_id: uuid.UUID, days_ahead: int = 7
    ) -> tuple[list[InventoryAlert], int, int, bool]:
        """The single deterministic pass behind AnalyzeInventoryTool: one query for
        every item needing attention, then a reorder recommendation for each
        (independent per-item reads, run concurrently rather than as N sequential
        round trips). Severity counts and action_required are computed here, not by
        the LLM, from real tool/DB output only."""
        flagged_items = await self.list_alerts(restaurant_id=restaurant_id)
        alerts = list(await asyncio.gather(*(self._build_alert(item, days_ahead=days_ahead) for item in flagged_items)))
        critical_count = sum(1 for a in alerts if a.severity == "critical")
        warning_count = sum(1 for a in alerts if a.severity == "warning")
        return alerts, critical_count, warning_count, bool(alerts)

    async def check_stock(
        self,
        *,
        restaurant_id: uuid.UUID,
        item_id: uuid.UUID,
        required_quantity: Decimal | None,
        context: ToolContext | None = None,
    ) -> tuple[InventoryItem, bool | None, Decimal | None]:
        item = await self._get_item_or_raise(restaurant_id, item_id)
        if required_quantity is None:
            return item, None, None
        sufficient = item.quantity_on_hand >= required_quantity
        shortfall = None if sufficient else (required_quantity - item.quantity_on_hand)
        if not sufficient:
            await self._publish(
                "inventory.low",
                restaurant_id=restaurant_id,
                entity_id=item.id,
                payload={
                    "item_id": str(item.id),
                    "item_name": item.name,
                    "quantity_on_hand": str(item.quantity_on_hand),
                    "low_stock_threshold": str(item.low_stock_threshold),
                    "required_quantity": str(required_quantity),
                    "shortfall": str(shortfall),
                },
                correlation_id=context.agent_run_id if context else None,
            )
        return item, sufficient, shortfall

    async def calculate_required_inventory(
        self, *, restaurant_id: uuid.UUID, item_id: uuid.UUID, days_ahead: int
    ) -> tuple[InventoryItem, Decimal, Decimal, Decimal, int]:
        item = await self._get_item_or_raise(restaurant_id, item_id)
        lookback_days = self.settings.inventory_usage_lookback_days
        since = utcnow() - timedelta(days=lookback_days)
        transactions = await self.repo.list_transactions_since(item_id, since)

        total_consumed = sum((-t.quantity_delta for t in transactions if t.quantity_delta < 0), Decimal(0))
        average_daily_usage = (Decimal(total_consumed) / Decimal(lookback_days)) if lookback_days > 0 else Decimal(0)
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

        # Idempotency: a second proposal for the same still-open shortage (e.g. the
        # inventory-check workflow running twice before anything has actually
        # changed the item's quantity) must not create a second PurchaseRequest/
        # Approval — it reports the existing open one instead of mutating again.
        existing = await self.repo.get_open_purchase_request_for_item(item_id)
        if existing is not None:
            if existing.status == "pending_approval" and existing.approval_id is not None:
                return PendingApprovalOutput(
                    approval_id=existing.approval_id,
                    summary=f"A purchase request for this item is already pending approval ({existing.approval_id}).",
                )
            return existing

        high_impact = (
            estimated_cost is not None and estimated_cost >= self.settings.purchase_request_high_impact_cost_threshold
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
            await self._publish(
                "purchase.requested",
                restaurant_id=restaurant_id,
                entity_id=purchase_request.id,
                payload={
                    "purchase_request_id": str(purchase_request.id),
                    "item_id": str(item_id),
                    "requested_quantity": str(requested_quantity),
                    "estimated_cost": str(estimated_cost),
                    "status": "pending_approval",
                },
                correlation_id=context.agent_run_id,
            )
            return PendingApprovalOutput(approval_id=approval.id, summary=approval.reason)

        purchase_request = await self.repo.create_purchase_request(
            restaurant_id=restaurant_id,
            item_id=item_id,
            requested_quantity=requested_quantity,
            estimated_cost=estimated_cost,
            status="approved",
            requested_by_agent_run_id=context.agent_run_id,
        )
        await self._publish(
            "purchase.requested",
            restaurant_id=restaurant_id,
            entity_id=purchase_request.id,
            payload={
                "purchase_request_id": str(purchase_request.id),
                "item_id": str(item_id),
                "requested_quantity": str(requested_quantity),
                "estimated_cost": str(estimated_cost) if estimated_cost is not None else None,
                "status": "approved",
            },
            correlation_id=context.agent_run_id,
        )
        return purchase_request

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
            raise ToolError("purchase_request_not_found", f"No purchase request found with id {purchase_request_id}")
        purchase_request.status = "approved"
        return await self.repo.save_purchase_request(purchase_request)
