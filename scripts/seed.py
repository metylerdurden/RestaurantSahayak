"""Seed one realistic restaurant's worth of MVP data.

Idempotent: clears DineOps' own tables (in FK-safe order) before inserting, so it can
be re-run freely against a dev database. Does NOT touch Approval/Event/AgentRun/
AgentMessage rows — those are runtime-generated, not seed data (see
specs/001-core-platform-mvp/tasks.md Phase 2).

Includes a handful of realistic Memory rows with real BGE-M3 embeddings, so semantic
recall has something meaningful to search over immediately after seeding.

Usage:
    uv run python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.embeddings.factory import get_embedding_provider
from app.models import (
    Customer,
    InventoryItem,
    MenuItem,
    Memory,
    Reservation,
    Restaurant,
    Sale,
    Staff,
    StaffShift,
    ShiftAssignment,
    Table,
    User,
)

RNG = random.Random(20260819)


async def _clear_existing(session: AsyncSession) -> None:
    """Delete in reverse-dependency order. Approval/Event/AgentRun/AgentMessage are
    intentionally left untouched — they're runtime-generated, not reseeded here."""
    for model in [Sale, Memory, ShiftAssignment, StaffShift, Reservation, Staff,
                  MenuItem, InventoryItem, Table, Customer, User, Restaurant]:
        await session.execute(delete(model))
    await session.commit()


async def _seed_restaurant(session: AsyncSession) -> Restaurant:
    restaurant = Restaurant(
        name="The Olive Table",
        timezone="America/New_York",
        address="214 Harbor St, Portland, ME 04101",
        phone="+1-207-555-0142",
    )
    session.add(restaurant)
    await session.flush()
    return restaurant


async def _seed_user(session: AsyncSession, restaurant: Restaurant) -> User:
    manager = User(
        restaurant_id=restaurant.id,
        name="Jordan Ellis",
        email="jordan@theolivetable.example",
        password_hash="not-implemented-mvp-seed",  # auth is out of scope for MVP (spec Assumptions)
        role="manager",
    )
    session.add(manager)
    await session.flush()
    return manager


async def _seed_tables(session: AsyncSession, restaurant: Restaurant) -> list[Table]:
    layout = [
        ("T1", 2), ("T2", 2), ("T3", 4), ("T4", 4),
        ("T5", 4), ("T6", 6), ("T7", 6), ("T8", 8),
    ]
    tables = [Table(restaurant_id=restaurant.id, label=label, seat_capacity=cap) for label, cap in layout]
    session.add_all(tables)
    await session.flush()
    return tables


async def _seed_menu_items(session: AsyncSession, restaurant: Restaurant) -> list[MenuItem]:
    items = [
        ("Marinated Olives & Feta", "Appetizer", 9.00),
        ("Grilled Halloumi", "Appetizer", 11.50),
        ("Hummus with Warm Pita", "Appetizer", 8.50),
        ("Charred Octopus", "Appetizer", 16.00),
        ("Lamb Souvlaki Plate", "Entree", 26.00),
        ("Roasted Branzino", "Entree", 29.00),
        ("Chicken Shawarma Bowl", "Entree", 21.00),
        ("Wild Mushroom Orzo (V)", "Entree", 19.00),
        ("Moussaka", "Entree", 23.00),
        ("Baklava", "Dessert", 8.00),
        ("Greek Yogurt with Honey & Walnuts", "Dessert", 7.00),
        ("Orange Olive Oil Cake", "Dessert", 8.50),
        ("House Red (glass)", "Beverage", 12.00),
        ("House White (glass)", "Beverage", 12.00),
        ("Sparkling Water", "Beverage", 4.00),
        ("Fresh Mint Lemonade", "Beverage", 6.00),
    ]
    menu_items = [
        MenuItem(restaurant_id=restaurant.id, name=name, category=category, price=price)
        for name, category, price in items
    ]
    session.add_all(menu_items)
    await session.flush()
    return menu_items


async def _seed_inventory(session: AsyncSession, restaurant: Restaurant) -> list[InventoryItem]:
    # (name, unit, quantity_on_hand, low_stock_threshold)
    stock = [
        ("Extra Virgin Olive Oil", "liter", 18.0, 5.0),
        ("Roma Tomatoes", "kg", 12.0, 4.0),
        ("Feta Cheese", "kg", 6.0, 2.0),
        ("Lamb Shoulder", "kg", 9.0, 3.0),
        ("Chicken Thigh", "kg", 14.0, 4.0),
        ("All-Purpose Flour", "kg", 20.0, 5.0),
        ("Lemons", "each", 60.0, 20.0),
        ("House Red Wine", "bottle", 24.0, 6.0),
        ("House White Wine", "bottle", 3.0, 6.0),   # seeded LOW on purpose
        ("Unsalted Butter", "kg", 5.0, 2.0),
        ("Garlic", "kg", 3.0, 1.0),
        ("Fresh Basil", "kg", 0.0, 1.0),             # seeded OUT OF STOCK on purpose
    ]
    inventory_items = []
    for name, unit, qty, threshold in stock:
        status = "out_of_stock" if qty == 0 else ("low" if qty <= threshold else "ok")
        inventory_items.append(
            InventoryItem(
                restaurant_id=restaurant.id,
                name=name,
                unit=unit,
                quantity_on_hand=qty,
                low_stock_threshold=threshold,
                status=status,
            )
        )
    session.add_all(inventory_items)
    await session.flush()
    return inventory_items


async def _seed_staff(session: AsyncSession, restaurant: Restaurant) -> list[Staff]:
    roster = [
        ("Alex Rivera", "server"), ("Priya Nair", "server"),
        ("Sam Okafor", "host"),
        ("Marco Bellini", "cook"), ("Dana Kim", "cook"),
        ("Theo Marsh", "bartender"),
    ]
    staff = [Staff(restaurant_id=restaurant.id, name=name, role=role) for name, role in roster]
    session.add_all(staff)
    await session.flush()
    return staff


async def _seed_shifts_and_assignments(
    session: AsyncSession, restaurant: Restaurant, staff: list[Staff]
) -> None:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    servers = [s for s in staff if s.role == "server"]
    cooks = [s for s in staff if s.role == "cook"]
    host = next(s for s in staff if s.role == "host")
    bartender = next(s for s in staff if s.role == "bartender")

    understaffed_injected = False
    for day_offset in range(7):
        day = today + timedelta(days=day_offset)
        for meal, start_hour, end_hour in (("lunch", 11, 15), ("dinner", 17, 22)):
            shift = StaffShift(
                restaurant_id=restaurant.id,
                start_at=day.replace(hour=start_hour),
                end_at=day.replace(hour=end_hour),
                required_staff_count=4,
                is_published=True,
            )
            session.add(shift)
            await session.flush()

            assigned = list(servers) + [cooks[day_offset % len(cooks)], host if meal == "dinner" else bartender]

            # Intentionally understaff exactly one upcoming dinner shift so Phase 5/10
            # event-driven testing has a ready-made fixture, per tasks.md Phase 2.
            if meal == "dinner" and day_offset == 2 and not understaffed_injected:
                assigned = [servers[0]]
                understaffed_injected = True

            for member in assigned:
                session.add(ShiftAssignment(shift_id=shift.id, staff_id=member.id))

            shift.status = "staffed" if len(assigned) >= shift.required_staff_count else "understaffed"

    await session.flush()


async def _seed_customers(session: AsyncSession, restaurant: Restaurant) -> list[Customer]:
    roster = [
        ("The Patels", "+1-207-555-0110", "patels@example.com"),
        ("Maria Gonzalez", "+1-207-555-0111", "maria.g@example.com"),
        ("David Chen", "+1-207-555-0112", "dchen@example.com"),
        ("Sofia Rossi", "+1-207-555-0113", "sofia.rossi@example.com"),
        ("The Whitfield Family", "+1-207-555-0114", None),
        ("James O'Brien", "+1-207-555-0115", "jobrien@example.com"),
        ("Aisha Khan", "+1-207-555-0116", "aisha.khan@example.com"),
        ("Liam Murphy", "+1-207-555-0117", None),
        ("Yuki Tanaka", "+1-207-555-0118", "yuki.t@example.com"),
        ("The Andersons", "+1-207-555-0119", "andersons@example.com"),
        ("Grace Okonkwo", "+1-207-555-0120", "grace.o@example.com"),
        ("Tom Reilly", None, "tom.reilly@example.com"),
    ]
    customers = [Customer(restaurant_id=restaurant.id, name=n, phone=p, email=e) for n, p, e in roster]
    session.add_all(customers)
    await session.flush()
    return customers


async def _seed_reservations_and_sales(
    session: AsyncSession,
    restaurant: Restaurant,
    customers: list[Customer],
    tables: list[Table],
    menu_items: list[MenuItem],
) -> None:
    now = datetime.now(timezone.utc)

    # Past reservations: a mix of completed and one no-show, feeding SC/analytics.
    past_specs = [
        (customers[0], tables[5], 6, -3, 19, "completed"),
        (customers[1], tables[2], 4, -2, 12, "completed"),
        (customers[2], tables[3], 4, -2, 19, "completed"),
        (customers[3], tables[0], 2, -1, 20, "no_show"),
        (customers[6], tables[6], 6, -6, 19, "completed"),
    ]
    for customer, table, party_size, day_offset, hour, status in past_specs:
        reservation = Reservation(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            table_id=table.id,
            party_size=party_size,
            requested_time=(now + timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0, microsecond=0),
            status=status,
            created_via="manager_request",
        )
        session.add(reservation)
        await session.flush()

        if status == "completed":
            for _ in range(party_size):
                item = RNG.choice(menu_items)
                qty = RNG.randint(1, 2)
                session.add(
                    Sale(
                        restaurant_id=restaurant.id,
                        reservation_id=reservation.id,
                        customer_id=customer.id,
                        menu_item_id=item.id,
                        quantity=qty,
                        unit_price=item.price,
                        sold_at=reservation.requested_time + timedelta(minutes=40),
                    )
                )

    # Upcoming reservations: routine bookings plus one large party (>= high-impact
    # threshold) so the approval-gated cancellation path has a ready fixture.
    upcoming_specs = [
        (customers[4], tables[7], 8, 1, 19, "booked"),   # large party — future approval-gated test fixture
        (customers[5], tables[2], 4, 1, 20, "booked"),
        (customers[7], tables[1], 2, 2, 18, "requested"),
        (customers[8], tables[4], 4, 3, 19, "booked"),
        (customers[9], tables[6], 6, 4, 19, "booked"),
    ]
    for customer, table, party_size, day_offset, hour, status in upcoming_specs:
        session.add(
            Reservation(
                restaurant_id=restaurant.id,
                customer_id=customer.id,
                table_id=table.id,
                party_size=party_size,
                requested_time=(now + timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0, microsecond=0),
                status=status,
                created_via="manager_request",
            )
        )
    await session.flush()


async def _seed_memories(session: AsyncSession, restaurant: Restaurant, customers: list[Customer]) -> None:
    """A handful of realistic memories with real BGE-M3 embeddings — semantic recall
    has something meaningful to search from immediately after seeding."""
    embedder = get_embedding_provider()

    memory_specs = [
        (
            customers[0], "CUSTOMER_PREFERENCE", "seating_preference",
            {"text": "The Patels always request a window table, away from the kitchen."},
            "manager_stated", 4, 1.00,
        ),
        (
            customers[0], "CUSTOMER_PREFERENCE", "dietary_note",
            {"text": "One member of the Patel party has a shellfish allergy."},
            "manager_stated", 5, 1.00,
        ),
        (
            customers[2], "CUSTOMER_PREFERENCE", "occasion",
            {"text": "David Chen usually books for work dinners and prefers a quiet corner table."},
            "agent_inferred", 3, 0.75,
        ),
        (None, "BUSINESS_RULE", "cancellation_policy",
         {"text": "Parties of 6 or more require 24 hours notice to cancel without a fee."},
         "manager_stated", 5, 1.00),
        (None, "OPERATIONAL_FACT", "peak_hours",
         {"text": "Friday and Saturday 7-9pm are consistently the busiest dinner service hours."},
         "system_derived", 3, 0.90),
    ]

    contents_to_embed = [spec[3]["text"] for spec in memory_specs]
    vectors = embedder.embed(contents_to_embed)

    for (customer, memory_type, topic, content, source, importance, confidence), vector in zip(
        memory_specs, vectors
    ):
        session.add(
            Memory(
                restaurant_id=restaurant.id,
                customer_id=customer.id if customer else None,
                agent_name=None,
                memory_type=memory_type,
                topic=topic,
                content=content,
                embedding=vector,
                importance=importance,
                confidence=confidence,
                source=source,
            )
        )
    await session.flush()


async def seed() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        print("Clearing existing DineOps data ...")
        await _clear_existing(session)

        print("Seeding restaurant + manager ...")
        restaurant = await _seed_restaurant(session)
        await _seed_user(session, restaurant)

        print("Seeding tables, menu, inventory, staff ...")
        tables = await _seed_tables(session, restaurant)
        menu_items = await _seed_menu_items(session, restaurant)
        await _seed_inventory(session, restaurant)
        staff = await _seed_staff(session, restaurant)

        print("Seeding shift schedule (with one intentionally understaffed shift) ...")
        await _seed_shifts_and_assignments(session, restaurant, staff)

        print("Seeding customers, reservations, sales ...")
        customers = await _seed_customers(session, restaurant)
        await _seed_reservations_and_sales(session, restaurant, customers, tables, menu_items)

        print("Seeding memories with real BGE-M3 embeddings (loads the model if not cached) ...")
        await _seed_memories(session, restaurant, customers)

        await session.commit()
        print(f"Done. Restaurant id: {restaurant.id}")


if __name__ == "__main__":
    asyncio.run(seed())
