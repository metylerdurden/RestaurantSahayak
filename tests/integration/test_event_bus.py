"""EventBus persistence against real Postgres: every event type is publishable and
durably logged with the fields requested (event_id, event_type, restaurant_id,
entity_id, payload, created_at, correlation_id), idempotent publish is enforced at
the database level (not just in the bus's own logic), and domain services actually
publish real events at the right points. Reactive workflow behavior (EventBus ->
Workflow -> Agent) is covered separately in
tests/integration/test_event_driven_workflows.py."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models import Event
from app.models.event import EVENT_TYPES
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.event_repo import EventRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.event_bus import InProcessEventBus
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService
from app.tools.base import ToolContext
from app.tools.customer_tools import UpdateCustomerTool
from app.tools.inventory_tools import CheckStockTool, CreatePurchaseRequestTool
from app.tools.reservation_tools import CreateReservationTool
from tests.integration.factories import (
    future,
    make_agent_run,
    make_customer,
    make_inventory_item,
    make_restaurant,
    make_table,
    make_user,
)

pytestmark = pytest.mark.asyncio


async def test_every_declared_event_type_is_publishable_and_persisted(db_session):
    bus = InProcessEventBus(EventRepository(db_session))
    restaurant = await make_restaurant(db_session)

    for event_type in EVENT_TYPES:
        envelope = await bus.publish(
            event_type=event_type,
            restaurant_id=restaurant.id,
            entity_id=uuid.uuid4(),
            payload={"note": "smoke test"},
            published_by="test",
        )
        stored = await db_session.get(Event, envelope.event_id)
        assert stored is not None
        assert stored.event_type == event_type
        assert stored.handled is True
        assert stored.correlation_id is not None
        assert stored.created_at is not None


async def test_idempotency_key_is_unique_per_restaurant_at_the_database_level(db_session):
    restaurant = await make_restaurant(db_session)
    db_session.add(
        Event(
            event_type="reservation.created",
            restaurant_id=restaurant.id,
            entity_id=uuid.uuid4(),
            payload={},
            correlation_id=uuid.uuid4(),
            published_by="x",
            idempotency_key="dup-key",
        )
    )
    await db_session.flush()

    db_session.add(
        Event(
            event_type="reservation.created",
            restaurant_id=restaurant.id,
            entity_id=uuid.uuid4(),
            payload={},
            correlation_id=uuid.uuid4(),
            published_by="x",
            idempotency_key="dup-key",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_creating_a_reservation_publishes_a_real_event(db_session):
    bus = InProcessEventBus(EventRepository(db_session))
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session),
        CustomerRepository(db_session),
        approval_service,
        get_settings(),
        event_bus=bus,
    )
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=4)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 2, "requested_time": future().isoformat()}, context=context
    )

    events = (await db_session.execute(select(Event).where(Event.restaurant_id == restaurant.id))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "reservation.created"
    assert events[0].entity_id == created.reservation.id
    assert events[0].correlation_id == agent_run.id


async def test_checking_insufficient_stock_publishes_inventory_low(db_session):
    bus = InProcessEventBus(EventRepository(db_session))
    approval_service = ApprovalService(ApprovalRepository(db_session))
    inventory_service = InventoryService(
        InventoryRepository(db_session), approval_service, get_settings(), event_bus=bus
    )
    restaurant = await make_restaurant(db_session)
    item = await make_inventory_item(db_session, restaurant, name="Olive Oil", quantity_on_hand=2)

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")
    output = await CheckStockTool(inventory_service)(
        {"item_id": str(item.id), "required_quantity": "10"}, context=context
    )
    assert output.sufficient is False

    events = (await db_session.execute(select(Event).where(Event.restaurant_id == restaurant.id))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "inventory.low"
    assert events[0].entity_id == item.id


async def test_purchase_request_publishes_purchase_requested(db_session):
    bus = InProcessEventBus(EventRepository(db_session))
    approval_service = ApprovalService(ApprovalRepository(db_session), event_bus=bus)
    inventory_service = InventoryService(
        InventoryRepository(db_session), approval_service, get_settings(), event_bus=bus
    )
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="inventory")
    item = await make_inventory_item(db_session, restaurant, name="Flour")

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory", agent_run_id=agent_run.id
    )
    await CreatePurchaseRequestTool(inventory_service)(
        {"item_id": str(item.id), "requested_quantity": "50", "estimated_cost": "500"}, context=context
    )

    events = (await db_session.execute(select(Event).where(Event.restaurant_id == restaurant.id))).scalars().all()
    types = {e.event_type for e in events}
    assert "purchase.requested" in types
    assert "approval.created" in types


async def test_approving_a_purchase_publishes_purchase_approved_and_approval_completed(db_session):
    bus = InProcessEventBus(EventRepository(db_session))
    approval_service = ApprovalService(ApprovalRepository(db_session), event_bus=bus)
    inventory_service = InventoryService(
        InventoryRepository(db_session), approval_service, get_settings(), event_bus=bus
    )
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="inventory")
    item = await make_inventory_item(db_session, restaurant, name="Flour")

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory", agent_run_id=agent_run.id
    )
    result = await CreatePurchaseRequestTool(inventory_service)(
        {"item_id": str(item.id), "requested_quantity": "50", "estimated_cost": "500"}, context=context
    )

    await approval_service.approve(result.approval_id, user.id)

    events = (await db_session.execute(select(Event).where(Event.restaurant_id == restaurant.id))).scalars().all()
    types = [e.event_type for e in events]
    assert "purchase.approved" in types
    assert types.count("approval.completed") == 1


async def test_updating_a_customer_publishes_customer_updated(db_session):
    bus = InProcessEventBus(EventRepository(db_session))
    customer_service = CustomerService(CustomerRepository(db_session), event_bus=bus)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")
    await UpdateCustomerTool(customer_service)(
        {"customer_id": str(customer.id), "phone": "+15559998888"}, context=context
    )

    events = (await db_session.execute(select(Event).where(Event.restaurant_id == restaurant.id))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "customer.updated"
    assert events[0].payload["changed"] == {"phone": "+15559998888"}
