"""InventoryService business rules against mocked repositories — no database."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models import InventoryItem
from app.repositories.inventory_repo import InventoryRepository
from app.services.approval_service import ApprovalService
from app.services.inventory_service import InventoryService
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError

RESTAURANT_ID = uuid.uuid4()


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        purchase_request_high_impact_cost_threshold=200,
    )


def _context(agent_run_id: uuid.UUID | None = None) -> ToolContext:
    return ToolContext(
        restaurant_id=RESTAURANT_ID, correlation_id="c1", acting_agent="inventory", agent_run_id=agent_run_id
    )


def _item(**overrides) -> InventoryItem:
    defaults = dict(
        id=uuid.uuid4(),
        restaurant_id=RESTAURANT_ID,
        name="Flour",
        unit="kg",
        quantity_on_hand=Decimal("5"),
        low_stock_threshold=Decimal("2"),
        status="ok",
    )
    defaults.update(overrides)
    return InventoryItem(**defaults)


@pytest.mark.asyncio
async def test_check_stock_raises_for_unknown_item():
    repo = AsyncMock(spec=InventoryRepository)
    repo.get_item.return_value = None
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    with pytest.raises(ToolError) as exc_info:
        await service.check_stock(restaurant_id=RESTAURANT_ID, item_id=uuid.uuid4(), required_quantity=None)
    assert exc_info.value.code == "item_not_found"


@pytest.mark.asyncio
async def test_check_stock_reports_shortfall_when_insufficient():
    repo = AsyncMock(spec=InventoryRepository)
    repo.get_item.return_value = _item(quantity_on_hand=Decimal("3"))
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    item, sufficient, shortfall = await service.check_stock(
        restaurant_id=RESTAURANT_ID, item_id=uuid.uuid4(), required_quantity=Decimal("5")
    )
    assert sufficient is False
    assert shortfall == Decimal("2")


@pytest.mark.asyncio
async def test_check_stock_no_required_quantity_reports_none():
    repo = AsyncMock(spec=InventoryRepository)
    repo.get_item.return_value = _item()
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    item, sufficient, shortfall = await service.check_stock(
        restaurant_id=RESTAURANT_ID, item_id=uuid.uuid4(), required_quantity=None
    )
    assert sufficient is None
    assert shortfall is None


@pytest.mark.asyncio
async def test_create_purchase_request_below_threshold_auto_approves():
    repo = AsyncMock(spec=InventoryRepository)
    repo.get_item.return_value = _item()
    repo.create_purchase_request.side_effect = lambda **kw: type("PR", (), {**kw, "id": uuid.uuid4()})()
    approval_service = AsyncMock(spec=ApprovalService)
    service = InventoryService(repo, approval_service, _settings())

    result = await service.create_purchase_request(
        restaurant_id=RESTAURANT_ID,
        item_id=uuid.uuid4(),
        requested_quantity=Decimal("10"),
        estimated_cost=Decimal("50"),
        context=_context(),
    )
    assert result.status == "approved"
    approval_service.create_approval_request.assert_not_called()


@pytest.mark.asyncio
async def test_create_purchase_request_above_threshold_requires_approval():
    repo = AsyncMock(spec=InventoryRepository)
    repo.get_item.return_value = _item()
    pr_stub = type("PR", (), {"id": uuid.uuid4(), "approval_id": None})()
    repo.create_purchase_request.return_value = pr_stub
    approval_service = AsyncMock(spec=ApprovalService)
    approval_stub = type("Approval", (), {"id": uuid.uuid4(), "reason": "Purchase 50 units"})()
    approval_service.create_approval_request.return_value = approval_stub
    service = InventoryService(repo, approval_service, _settings())

    result = await service.create_purchase_request(
        restaurant_id=RESTAURANT_ID,
        item_id=uuid.uuid4(),
        requested_quantity=Decimal("50"),
        estimated_cost=Decimal("500"),
        context=_context(agent_run_id=uuid.uuid4()),
    )
    assert isinstance(result, PendingApprovalOutput)
    assert result.approval_id == approval_stub.id


@pytest.mark.asyncio
async def test_create_purchase_request_above_threshold_without_agent_run_raises():
    repo = AsyncMock(spec=InventoryRepository)
    repo.get_item.return_value = _item()
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    with pytest.raises(ToolError) as exc_info:
        await service.create_purchase_request(
            restaurant_id=RESTAURANT_ID,
            item_id=uuid.uuid4(),
            requested_quantity=Decimal("50"),
            estimated_cost=Decimal("500"),
            context=_context(),
        )
    assert exc_info.value.code == "agent_run_required"


@pytest.mark.asyncio
async def test_calculate_required_inventory_formula():
    repo = AsyncMock(spec=InventoryRepository)
    item = _item(quantity_on_hand=Decimal("10"), low_stock_threshold=Decimal("4"))
    repo.get_item.return_value = item

    # 14-day lookback (default), total consumption of 28 -> avg 2/day.
    fake_txns = [
        type("Txn", (), {"quantity_delta": Decimal("-28")})(),
        type("Txn", (), {"quantity_delta": Decimal("5")})(),  # positive deltas ignored (deliveries)
    ]
    repo.list_transactions_since.return_value = fake_txns
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    result_item, avg_usage, projected, recommended, lookback_days = await service.calculate_required_inventory(
        restaurant_id=RESTAURANT_ID, item_id=uuid.uuid4(), days_ahead=7
    )
    assert lookback_days == 14
    assert avg_usage == Decimal("2")
    assert projected == Decimal("10") - Decimal("2") * 7  # -4
    assert recommended == (Decimal("4") * 2) - projected  # target buffer - projected
