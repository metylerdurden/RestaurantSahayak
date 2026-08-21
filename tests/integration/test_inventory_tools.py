"""Inventory tools end-to-end against real Postgres, including the create_purchase_request
approval round trip."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models import InventoryTransaction
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.inventory_repo import InventoryRepository
from app.services.approval_service import ApprovalService
from app.services.inventory_service import InventoryService
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError, utcnow
from app.tools.inventory_tools import (
    AnalyzeInventoryTool,
    CalculateReorderQuantityTool,
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)
from tests.integration.factories import make_agent_run, make_inventory_item, make_restaurant, make_user

pytestmark = pytest.mark.asyncio


async def _build(db_session):
    from app.core.config import get_settings

    repo = InventoryRepository(db_session)
    approval_service = ApprovalService(ApprovalRepository(db_session))
    service = InventoryService(repo, approval_service, get_settings())
    return service, approval_service


async def test_get_inventory_filters_by_status(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    await make_inventory_item(db_session, restaurant, quantity_on_hand=0, low_stock_threshold=2, name="Basil")
    await make_inventory_item(db_session, restaurant, quantity_on_hand=20, low_stock_threshold=2, name="Rice")
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    output = await GetInventoryTool(service)({"status": "out_of_stock"}, context=context)
    assert len(output.items) == 1
    assert output.items[0].name == "Basil"


async def test_check_stock_end_to_end(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(db_session, restaurant, quantity_on_hand=3, low_stock_threshold=2)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    output = await CheckStockTool(service)({"item_id": str(item.id), "required_quantity": "5"}, context=context)
    assert output.sufficient is False
    assert output.shortfall == Decimal("2")


async def test_check_stock_unknown_item_raises(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    with pytest.raises(ToolError) as exc_info:
        import uuid as uuid_mod

        await CheckStockTool(service)({"item_id": str(uuid_mod.uuid4())}, context=context)
    assert exc_info.value.code == "item_not_found"


async def test_calculate_required_inventory_uses_real_transaction_history(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(db_session, restaurant, quantity_on_hand=10, low_stock_threshold=4)

    # 14 kg consumed over the last 7 days -> avg 1 kg/day over the 14-day lookback window.
    db_session.add(
        InventoryTransaction(
            restaurant_id=restaurant.id,
            item_id=item.id,
            change_type="waste",
            quantity_delta=Decimal("-14"),
            resulting_quantity=Decimal("10"),
            recorded_at=utcnow() - timedelta(days=3),
        )
    )
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")
    output = await CalculateRequiredInventoryTool(service)({"item_id": str(item.id), "days_ahead": 7}, context=context)
    assert output.average_daily_usage == Decimal("1")
    assert output.lookback_days_used == 14


async def test_create_purchase_request_full_approval_round_trip(db_session):
    service, approval_service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="inventory")
    item = await make_inventory_item(db_session, restaurant)

    context = ToolContext(
        restaurant_id=restaurant.id,
        correlation_id="c1",
        acting_agent="inventory",
        agent_run_id=agent_run.id,
    )
    result = await CreatePurchaseRequestTool(service)(
        {"item_id": str(item.id), "requested_quantity": "100", "estimated_cost": "500"}, context=context
    )
    assert isinstance(result, PendingApprovalOutput)

    approval = await approval_service.approve(result.approval_id, user.id)
    purchase_request = await service.execute_approved_action(approval)
    assert purchase_request.status == "approved"


async def test_create_purchase_request_low_cost_auto_approves(db_session):
    service, approval_service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(db_session, restaurant)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    output = await CreatePurchaseRequestTool(service)(
        {"item_id": str(item.id), "requested_quantity": "5", "estimated_cost": "20"}, context=context
    )
    assert output.purchase_request.status == "approved"


async def test_create_purchase_request_rejected_leaves_it_unexecuted(db_session):
    """TEST 8: manager rejects the proposed purchase -> the request is never executed
    (it stays exactly as it was when raised, not silently mutated to 'approved')."""
    service, approval_service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="inventory")
    item = await make_inventory_item(db_session, restaurant)
    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory", agent_run_id=agent_run.id
    )

    result = await CreatePurchaseRequestTool(service)(
        {"item_id": str(item.id), "requested_quantity": "100", "estimated_cost": "500"}, context=context
    )
    assert isinstance(result, PendingApprovalOutput)

    await approval_service.reject(result.approval_id, user.id)

    stored = await service.repo.get_open_purchase_request_for_item(item.id)
    assert stored is not None
    assert stored.status == "pending_approval"  # not "approved" — rejection never executes


async def test_duplicate_purchase_request_for_the_same_open_shortage_is_not_created_twice(db_session):
    """TEST 11: proposing a purchase request for an item that already has one open
    (e.g. the inventory-check workflow running twice before anything restocks the
    item) must not create a second PurchaseRequest/Approval."""
    service, approval_service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="inventory")
    item = await make_inventory_item(db_session, restaurant, quantity_on_hand=0, low_stock_threshold=5)
    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory", agent_run_id=agent_run.id
    )

    first = await CreatePurchaseRequestTool(service)(
        {"item_id": str(item.id), "requested_quantity": "40", "estimated_cost": "600"}, context=context
    )
    second = await CreatePurchaseRequestTool(service)(
        {"item_id": str(item.id), "requested_quantity": "40", "estimated_cost": "600"}, context=context
    )

    assert isinstance(first, PendingApprovalOutput)
    assert isinstance(second, PendingApprovalOutput)
    assert first.approval_id == second.approval_id  # the same open approval, not a new one

    from sqlalchemy import select as _select

    from app.models import PurchaseRequest as _PurchaseRequest

    rows = (
        (await db_session.execute(_select(_PurchaseRequest).where(_PurchaseRequest.item_id == item.id))).scalars().all()
    )
    assert len(rows) == 1


async def test_analyze_inventory_tool_returns_classified_alerts_in_one_call(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    await make_inventory_item(db_session, restaurant, name="Fresh Basil", quantity_on_hand=0, low_stock_threshold=1)
    await make_inventory_item(
        db_session, restaurant, name="House White Wine", quantity_on_hand=3, low_stock_threshold=6
    )
    await make_inventory_item(db_session, restaurant, name="Rice", quantity_on_hand=20, low_stock_threshold=2)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    output = await AnalyzeInventoryTool(service)({}, context=context)

    assert output.critical_count == 1
    assert output.warning_count == 1
    assert output.action_required is True
    names = {a.item.name for a in output.alerts}
    assert names == {"Fresh Basil", "House White Wine"}  # well-stocked "Rice" is not in the alert list
    basil = next(a for a in output.alerts if a.item.name == "Fresh Basil")
    assert basil.severity == "critical"
    assert basil.reorder_quantity_available is True


async def test_calculate_reorder_quantity_tool_end_to_end(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(
        db_session, restaurant, name="Fresh Basil", quantity_on_hand=0, low_stock_threshold=1
    )
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    output = await CalculateReorderQuantityTool(service)({"item_id": str(item.id)}, context=context)

    assert output.item_name == "Fresh Basil"
    assert output.current_quantity == Decimal("0")
    assert output.threshold == Decimal("1")
    assert output.target_quantity == Decimal("2")
    assert output.recommended_order_quantity == Decimal("2")
    assert output.unit == "kg"


async def test_calculate_reorder_quantity_tool_unknown_item_raises(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")

    with pytest.raises(ToolError) as exc_info:
        import uuid as uuid_mod

        await CalculateReorderQuantityTool(service)({"item_id": str(uuid_mod.uuid4())}, context=context)
    assert exc_info.value.code == "item_not_found"
