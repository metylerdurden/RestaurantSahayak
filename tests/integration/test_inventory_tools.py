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
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError
from app.tools.inventory_tools import (
    CalculateRequiredInventoryTool,
    CheckStockTool,
    CreatePurchaseRequestTool,
    GetInventoryTool,
)
from app.tools.base import utcnow
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

    output = await CheckStockTool(service)(
        {"item_id": str(item.id), "required_quantity": "5"}, context=context
    )
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
    output = await CalculateRequiredInventoryTool(service)(
        {"item_id": str(item.id), "days_ahead": 7}, context=context
    )
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

    approval = await approval_service.decide(result.approval_id, "approved", user.id)
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
