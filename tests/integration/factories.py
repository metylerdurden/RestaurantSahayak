"""Shared entity-creation helpers for Phase 3 integration tests. Plain async
functions (not fixtures) taking a session — mirrors the pattern already used in
tests/integration/test_models.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models import (
    AgentRun,
    Customer,
    InventoryItem,
    MenuItem,
    Restaurant,
    Staff,
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


async def make_agent_run(session, restaurant: Restaurant, user: User, *, agent_name: str = "reservation") -> AgentRun:
    run = AgentRun(
        restaurant_id=restaurant.id,
        agent_name=agent_name,
        correlation_id=uuid.uuid4(),
        trigger_type="manager_request",
        initiated_by_user_id=user.id,
    )
    session.add(run)
    await session.flush()
    return run


def future(days: int = 1, hour: int = 19) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
