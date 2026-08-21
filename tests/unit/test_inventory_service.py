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
    repo.get_open_purchase_request_for_item.return_value = None
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
    repo.get_open_purchase_request_for_item.return_value = None
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
    repo.get_open_purchase_request_for_item.return_value = None
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


# --- analyze_inventory / list_alerts: deterministic classification (TEST 1-6) ---


def _service_with_items(*items) -> InventoryService:
    repo = AsyncMock(spec=InventoryRepository)
    repo.list_items.return_value = list(items)
    items_by_id = {i.id: i for i in items}
    repo.get_item.side_effect = lambda item_id: items_by_id.get(item_id)
    repo.list_transactions_since.return_value = []
    return InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())


@pytest.mark.asyncio
async def test_all_inventory_normal_requires_no_action():
    """TEST 1: every item well-stocked -> no alerts at all."""
    service = _service_with_items(
        _item(name="Flour", quantity_on_hand=Decimal("20"), low_stock_threshold=Decimal("5")),
        _item(name="Sugar", quantity_on_hand=Decimal("15"), low_stock_threshold=Decimal("3")),
    )

    alerts, critical_count, warning_count, action_required = await service.analyze_inventory(
        restaurant_id=RESTAURANT_ID
    )

    assert alerts == []
    assert critical_count == 0
    assert warning_count == 0
    assert action_required is False


@pytest.mark.asyncio
async def test_one_out_of_stock_item_is_critical():
    """TEST 2."""
    service = _service_with_items(
        _item(name="Basil", quantity_on_hand=Decimal("0"), low_stock_threshold=Decimal("1")),
        _item(name="Flour", quantity_on_hand=Decimal("20"), low_stock_threshold=Decimal("5")),
    )

    alerts, critical_count, warning_count, action_required = await service.analyze_inventory(
        restaurant_id=RESTAURANT_ID
    )

    assert critical_count == 1
    assert warning_count == 0
    assert action_required is True
    assert len(alerts) == 1
    assert alerts[0].status == "out_of_stock"
    assert alerts[0].severity == "critical"
    assert alerts[0].action_required is True


@pytest.mark.asyncio
async def test_one_low_stock_item_is_a_warning():
    """TEST 3."""
    service = _service_with_items(
        _item(name="White Wine", quantity_on_hand=Decimal("3"), low_stock_threshold=Decimal("6")),
        _item(name="Flour", quantity_on_hand=Decimal("20"), low_stock_threshold=Decimal("5")),
    )

    alerts, critical_count, warning_count, action_required = await service.analyze_inventory(
        restaurant_id=RESTAURANT_ID
    )

    assert critical_count == 0
    assert warning_count == 1
    assert action_required is True
    assert alerts[0].status == "low"
    assert alerts[0].severity == "warning"


@pytest.mark.asyncio
async def test_one_out_of_stock_and_one_low_stock_item_are_both_flagged():
    """TEST 4."""
    service = _service_with_items(
        _item(name="Basil", quantity_on_hand=Decimal("0"), low_stock_threshold=Decimal("1")),
        _item(name="White Wine", quantity_on_hand=Decimal("3"), low_stock_threshold=Decimal("6")),
        _item(name="Flour", quantity_on_hand=Decimal("20"), low_stock_threshold=Decimal("5")),
    )

    alerts, critical_count, warning_count, action_required = await service.analyze_inventory(
        restaurant_id=RESTAURANT_ID
    )

    assert critical_count == 1
    assert warning_count == 1
    assert action_required is True
    assert len(alerts) == 2
    statuses = {a.status for a in alerts}
    assert statuses == {"out_of_stock", "low"}


@pytest.mark.asyncio
async def test_reorder_quantity_unavailable_is_reported_not_hallucinated():
    """TEST 5: the per-item reorder calculation fails (simulated missing data via a
    now-vanished item) -> the alert says so honestly instead of inventing a number."""
    item = _item(name="Basil", quantity_on_hand=Decimal("0"), low_stock_threshold=Decimal("1"))
    repo = AsyncMock(spec=InventoryRepository)
    repo.list_items.return_value = [item]
    repo.get_item.return_value = None  # calculate_required_inventory's own lookup fails
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    alerts, critical_count, warning_count, action_required = await service.analyze_inventory(
        restaurant_id=RESTAURANT_ID
    )

    assert len(alerts) == 1
    assert alerts[0].recommended_reorder_quantity is None
    assert alerts[0].reorder_quantity_available is False
    assert alerts[0].reason  # a concrete explanation, not silence
    # still correctly flagged and counted, even without a reorder number
    assert alerts[0].action_required is True
    assert critical_count == 1


@pytest.mark.asyncio
async def test_reorder_quantity_calculated_deterministically_when_data_exists():
    """TEST 6: real usage history -> a real, reproducible reorder number, not a guess."""
    item = _item(name="Basil", quantity_on_hand=Decimal("0"), low_stock_threshold=Decimal("2"))
    repo = AsyncMock(spec=InventoryRepository)
    repo.list_items.return_value = [item]
    repo.get_item.return_value = item
    repo.list_transactions_since.return_value = [type("Txn", (), {"quantity_delta": Decimal("-14")})()]
    service = InventoryService(repo, AsyncMock(spec=ApprovalService), _settings())

    alerts, *_ = await service.analyze_inventory(restaurant_id=RESTAURANT_ID, days_ahead=7)

    # 14-day lookback (default), 14 consumed -> avg 1/day; projected = 0 - 1*7 = -7;
    # target buffer = threshold*2 = 4; recommended = 4 - (-7) = 11.
    assert alerts[0].reorder_quantity_available is True
    assert alerts[0].recommended_reorder_quantity == Decimal("11")
