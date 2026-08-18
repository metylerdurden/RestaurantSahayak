"""Runs the real seed script against the test database and verifies it produces
realistic, referentially-consistent data — including the two intentionally-triggered
fixtures (a low/out-of-stock inventory item, an understaffed shift) later phases rely
on, and that memories were seeded with real embeddings.

Not run inside the `db_session` (transaction-rollback) fixture — the seed script
manages its own session/commits, matching how it's actually invoked
(`uv run python scripts/seed.py`). Runs against `dineops_test` only (see
tests/conftest.py), never the dev database's demo data.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.db import get_session_factory
from app.models import (
    Customer,
    InventoryItem,
    Memory,
    MenuItem,
    Reservation,
    Restaurant,
    Sale,
    Staff,
    StaffShift,
    Table,
    User,
)
from scripts.seed import seed

pytestmark = pytest.mark.asyncio


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seed_produces_realistic_referentially_consistent_data(_migrated_test_db):
    await seed()

    session_factory = get_session_factory()
    async with session_factory() as session:
        assert await _count(session, Restaurant) == 1
        assert await _count(session, User) == 1
        assert await _count(session, Table) >= 8
        assert await _count(session, MenuItem) >= 15
        assert await _count(session, InventoryItem) >= 10
        assert await _count(session, Staff) >= 5
        assert await _count(session, StaffShift) >= 14
        assert await _count(session, Customer) >= 10
        assert await _count(session, Reservation) >= 10
        assert await _count(session, Sale) > 0
        assert await _count(session, Memory) >= 5

        low_stock_items = (
            await session.execute(select(InventoryItem).where(InventoryItem.status != "ok"))
        ).scalars().all()
        assert len(low_stock_items) >= 1, "seed data must include at least one low/out-of-stock item"

        understaffed = (
            await session.execute(select(StaffShift).where(StaffShift.status == "understaffed"))
        ).scalars().all()
        assert len(understaffed) >= 1, "seed data must include at least one understaffed shift"

        memories = (await session.execute(select(Memory))).scalars().all()
        assert all(m.embedding is not None for m in memories), "every seeded memory must have an embedding"
        assert all(len(m.embedding) == 1024 for m in memories)


async def test_seed_is_idempotent_and_rerunnable(_migrated_test_db):
    await seed()
    session_factory = get_session_factory()
    async with session_factory() as session:
        first_run_restaurants = await _count(session, Restaurant)

    await seed()  # re-run: should clear and reseed, not accumulate
    async with session_factory() as session:
        second_run_restaurants = await _count(session, Restaurant)

    assert first_run_restaurants == second_run_restaurants == 1
