"""Model/constraint verification against the real (test) database — every CHECK
constraint and unique constraint that matters is exercised at least once."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    AgentRun,
    Approval,
    Customer,
    InventoryItem,
    Memory,
    Reservation,
    Restaurant,
    Sale,
    MenuItem,
    Staff,
    StaffShift,
    ShiftAssignment,
    Table,
    User,
)

pytestmark = pytest.mark.asyncio


async def _restaurant(session) -> Restaurant:
    r = Restaurant(name=f"Test Restaurant {uuid.uuid4().hex[:8]}", timezone="UTC")
    session.add(r)
    await session.flush()
    return r


async def _customer(session, restaurant: Restaurant, **kwargs) -> Customer:
    c = Customer(restaurant_id=restaurant.id, name="Test Customer", phone="+15550000000", **kwargs)
    session.add(c)
    await session.flush()
    return c


async def _user(session, restaurant: Restaurant) -> User:
    u = User(
        restaurant_id=restaurant.id,
        name="Test Manager",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    session.add(u)
    await session.flush()
    return u


async def _agent_run(session, restaurant: Restaurant, user: User) -> AgentRun:
    run = AgentRun(
        restaurant_id=restaurant.id,
        agent_name="reservation",
        model_name="qwen3:8b",
        correlation_id=uuid.uuid4(),
        trigger_type="manager_request",
        initiated_by_user_id=user.id,
    )
    session.add(run)
    await session.flush()
    return run


# --- InventoryItem ---


async def test_inventory_item_rejects_negative_quantity(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(
        InventoryItem(restaurant_id=restaurant.id, name="Flour", unit="kg", quantity_on_hand=-1)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_inventory_item_rejects_invalid_status(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(
        InventoryItem(
            restaurant_id=restaurant.id, name="Flour", unit="kg", quantity_on_hand=5, status="spoiled"
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --- Customer ---


async def test_customer_requires_a_contact_method(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(Customer(restaurant_id=restaurant.id, name="No Contact"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_customer_with_only_email_is_valid(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(Customer(restaurant_id=restaurant.id, name="Email Only", email="a@b.com"))
    await db_session.flush()  # should not raise


# --- Reservation ---


async def test_reservation_rejects_invalid_status(db_session):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)
    db_session.add(
        Reservation(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            party_size=4,
            requested_time=datetime.now(timezone.utc) + timedelta(days=1),
            status="seated_forever",  # invalid
            created_via="manager_request",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_reservation_rejects_zero_party_size(db_session):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)
    db_session.add(
        Reservation(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            party_size=0,
            requested_time=datetime.now(timezone.utc) + timedelta(days=1),
            created_via="manager_request",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_reservation_valid_row_persists(db_session):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)
    table = Table(restaurant_id=restaurant.id, label="T1", seat_capacity=4)
    db_session.add(table)
    await db_session.flush()

    reservation = Reservation(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        table_id=table.id,
        party_size=4,
        requested_time=datetime.now(timezone.utc) + timedelta(days=1),
        created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()
    assert reservation.status == "requested"
    assert reservation.duration_minutes == 90


# --- Sale (generated column) ---


async def test_sale_total_price_is_computed_by_the_database(db_session):
    restaurant = await _restaurant(db_session)
    menu_item = MenuItem(restaurant_id=restaurant.id, name="Baklava", price=8.00)
    db_session.add(menu_item)
    await db_session.flush()

    sale = Sale(restaurant_id=restaurant.id, menu_item_id=menu_item.id, quantity=3, unit_price=8.00)
    db_session.add(sale)
    await db_session.flush()
    await db_session.refresh(sale)
    assert float(sale.total_price) == 24.00


# --- Approval ---


async def test_approval_cannot_be_approved_without_a_decision(db_session):
    restaurant = await _restaurant(db_session)
    user = await _user(db_session, restaurant)
    run = await _agent_run(db_session, restaurant, user)

    db_session.add(
        Approval(
            restaurant_id=restaurant.id,
            domain="reservation",
            proposed_by_tool="cancel_reservation",
            proposed_by_agent_run_id=run.id,
            proposed_action={"tool": "cancel_reservation", "args": {}},
            summary="Cancel a large party",
            status="approved",  # missing decided_by_user_id / decided_at
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_approval_with_full_decision_is_valid(db_session):
    restaurant = await _restaurant(db_session)
    user = await _user(db_session, restaurant)
    run = await _agent_run(db_session, restaurant, user)

    db_session.add(
        Approval(
            restaurant_id=restaurant.id,
            domain="reservation",
            proposed_by_tool="cancel_reservation",
            proposed_by_agent_run_id=run.id,
            proposed_action={"tool": "cancel_reservation", "args": {}},
            summary="Cancel a large party",
            status="approved",
            decided_by_user_id=user.id,
            decided_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()  # should not raise


# --- AgentRun ---


async def test_agent_run_trigger_consistency_rejects_both_null(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(
        AgentRun(
            restaurant_id=restaurant.id,
            agent_name="reservation",
            model_name="qwen3:8b",
            correlation_id=uuid.uuid4(),
            trigger_type="manager_request",
            initiated_by_user_id=None,  # required for manager_request
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --- ShiftAssignment ---


async def test_shift_assignment_rejects_duplicate_staff_on_same_shift(db_session):
    restaurant = await _restaurant(db_session)
    staff = Staff(restaurant_id=restaurant.id, name="Alex", role="server")
    shift = StaffShift(
        restaurant_id=restaurant.id,
        start_at=datetime.now(timezone.utc),
        end_at=datetime.now(timezone.utc) + timedelta(hours=4),
        required_staff_count=2,
    )
    db_session.add_all([staff, shift])
    await db_session.flush()

    db_session.add(ShiftAssignment(shift_id=shift.id, staff_id=staff.id))
    await db_session.flush()

    db_session.add(ShiftAssignment(shift_id=shift.id, staff_id=staff.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --- Memory (importance/confidence ranges; dedup is covered in test_memory_embeddings.py) ---


async def test_memory_rejects_importance_out_of_range(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(
        Memory(
            restaurant_id=restaurant.id,
            agent_name="reservation",
            memory_type="AGENT_EXPERIENCE",
            topic="test",
            content={"text": "x"},
            importance=6,  # out of 1-5 range
            source="agent_inferred",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_memory_rejects_invalid_memory_type(db_session):
    restaurant = await _restaurant(db_session)
    db_session.add(
        Memory(
            restaurant_id=restaurant.id,
            agent_name="reservation",
            memory_type="NOT_A_REAL_TYPE",
            topic="test",
            content={"text": "x"},
            source="agent_inferred",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
