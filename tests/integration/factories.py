"""Shared entity-creation helpers for Phase 3 integration tests. Plain async
functions (not fixtures) taking a session — mirrors the pattern already used in
tests/integration/test_models.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models import (
    AgentRun,
    Approval,
    Customer,
    Event,
    InventoryItem,
    MenuItem,
    Reservation,
    Restaurant,
    ShiftAssignment,
    Staff,
    StaffShift,
    Table,
    User,
)


async def make_restaurant(session) -> Restaurant:
    r = Restaurant(name=f"Test Restaurant {uuid.uuid4().hex[:8]}", timezone="UTC")
    session.add(r)
    await session.flush()
    return r


async def make_user(session, restaurant: Restaurant) -> User:
    u = User(
        restaurant_id=restaurant.id,
        name="Test Manager",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
    )
    session.add(u)
    await session.flush()
    return u


async def make_customer(session, restaurant: Restaurant, **kwargs) -> Customer:
    defaults = {"name": "Test Customer", "phone": f"+1555{uuid.uuid4().int % 10_000_000:07d}"}
    defaults.update(kwargs)
    c = Customer(restaurant_id=restaurant.id, **defaults)
    session.add(c)
    await session.flush()
    return c


async def make_table(session, restaurant: Restaurant, *, seat_capacity: int = 4, label: str | None = None) -> Table:
    t = Table(
        restaurant_id=restaurant.id,
        label=label or f"T-{uuid.uuid4().hex[:6]}",
        seat_capacity=seat_capacity,
    )
    session.add(t)
    await session.flush()
    return t


async def make_menu_item(session, restaurant: Restaurant, *, price=10.00, name: str | None = None) -> MenuItem:
    m = MenuItem(restaurant_id=restaurant.id, name=name or f"Item {uuid.uuid4().hex[:6]}", price=price)
    session.add(m)
    await session.flush()
    return m


async def make_inventory_item(
    session, restaurant: Restaurant, *, quantity_on_hand=10, low_stock_threshold=5, name: str | None = None
) -> InventoryItem:
    status = "out_of_stock" if quantity_on_hand == 0 else ("low" if quantity_on_hand <= low_stock_threshold else "ok")
    i = InventoryItem(
        restaurant_id=restaurant.id,
        name=name or f"Ingredient {uuid.uuid4().hex[:6]}",
        unit="kg",
        quantity_on_hand=quantity_on_hand,
        low_stock_threshold=low_stock_threshold,
        status=status,
    )
    session.add(i)
    await session.flush()
    return i


async def make_staff(session, restaurant: Restaurant, *, role: str = "server", name: str | None = None) -> Staff:
    s = Staff(restaurant_id=restaurant.id, name=name or f"Staff {uuid.uuid4().hex[:6]}", role=role)
    session.add(s)
    await session.flush()
    return s


async def make_agent_run(
    session, restaurant: Restaurant, user: User, *, agent_name: str = "reservation", model_name: str = "qwen3:8b"
) -> AgentRun:
    run = AgentRun(
        restaurant_id=restaurant.id,
        agent_name=agent_name,
        model_name=model_name,
        correlation_id=uuid.uuid4(),
        trigger_type="manager_request",
        initiated_by_user_id=user.id,
    )
    session.add(run)
    await session.flush()
    return run


async def make_reservation(
    session,
    restaurant: Restaurant,
    customer: Customer,
    table: Table,
    *,
    party_size: int = 4,
    requested_time: datetime | None = None,
    status: str = "booked",
) -> Reservation:
    r = Reservation(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        table_id=table.id,
        party_size=party_size,
        requested_time=requested_time or future(),
        status=status,
        created_via="manager_request",
    )
    session.add(r)
    await session.flush()
    return r


async def make_staff_shift(
    session,
    restaurant: Restaurant,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    required_staff_count: int = 4,
    status: str = "understaffed",
    is_published: bool = True,
) -> StaffShift:
    start = start_at or future(hour=18)
    end = end_at or (start + timedelta(hours=4))
    shift = StaffShift(
        restaurant_id=restaurant.id,
        start_at=start,
        end_at=end,
        required_staff_count=required_staff_count,
        status=status,
        is_published=is_published,
    )
    session.add(shift)
    await session.flush()
    return shift


async def make_shift_assignment(session, shift: StaffShift, staff: Staff) -> ShiftAssignment:
    a = ShiftAssignment(shift_id=shift.id, staff_id=staff.id)
    session.add(a)
    await session.flush()
    return a


async def make_approval(
    session,
    restaurant: Restaurant,
    agent_run: AgentRun,
    *,
    domain: str = "reservation",
    action: str = "cancel_reservation",
    agent_name: str = "reservation",
    risk_level: str = "MEDIUM",
    parameters: dict | None = None,
    reason: str = "Test approval",
) -> Approval:
    a = Approval(
        restaurant_id=restaurant.id,
        domain=domain,
        action=action,
        agent_name=agent_name,
        proposed_by_agent_run_id=agent_run.id,
        parameters=parameters or {},
        reason=reason,
        risk_level=risk_level,
        status="pending",
    )
    session.add(a)
    await session.flush()
    return a


async def make_event(
    session,
    restaurant: Restaurant,
    *,
    event_type: str = "reservation.created",
    entity_id: uuid.UUID | None = None,
    payload: dict | None = None,
    published_by: str = "test",
) -> Event:
    e = Event(
        restaurant_id=restaurant.id,
        event_type=event_type,
        entity_id=entity_id,
        payload=payload or {},
        correlation_id=uuid.uuid4(),
        published_by=published_by,
    )
    session.add(e)
    await session.flush()
    return e


def future(days: int = 1, hour: int = 19) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
