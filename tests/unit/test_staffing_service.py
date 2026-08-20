"""StaffingService business rules against mocked repository — no database. Focused on
the calculate_staff_requirement formula, since that's where the deterministic
business logic lives."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.repositories.staffing_repo import StaffingRepository
from app.services.staffing_service import StaffingService

RESTAURANT_ID = uuid.uuid4()


def _settings(**overrides) -> Settings:
    defaults = dict(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        covers_per_server=15,
        covers_per_cook=20,
        minimum_servers_per_shift=1,
        minimum_cooks_per_shift=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _window():
    start = datetime.now(timezone.utc)
    return start, start + timedelta(hours=4)


@pytest.mark.asyncio
async def test_calculate_staff_requirement_scales_with_explicit_covers():
    repo = AsyncMock(spec=StaffingRepository)
    service = StaffingService(repo, _settings())
    start, end = _window()

    covers, servers, cooks, host, total = await service.calculate_staff_requirement(
        restaurant_id=RESTAURANT_ID, start_at=start, end_at=end, expected_covers=60
    )
    assert covers == 60
    assert servers == 4  # ceil(60/15)
    assert cooks == 3  # ceil(60/20)
    assert host == 1
    assert total == 8
    repo.sum_expected_covers.assert_not_called()


@pytest.mark.asyncio
async def test_calculate_staff_requirement_applies_minimums_for_zero_covers():
    repo = AsyncMock(spec=StaffingRepository)
    service = StaffingService(repo, _settings())
    start, end = _window()

    covers, servers, cooks, host, total = await service.calculate_staff_requirement(
        restaurant_id=RESTAURANT_ID, start_at=start, end_at=end, expected_covers=0
    )
    assert servers == 1
    assert cooks == 1
    assert total == 3


@pytest.mark.asyncio
async def test_calculate_staff_requirement_derives_covers_from_reservations_when_not_given():
    repo = AsyncMock(spec=StaffingRepository)
    repo.sum_expected_covers.return_value = 45
    service = StaffingService(repo, _settings())
    start, end = _window()

    covers, servers, cooks, host, total = await service.calculate_staff_requirement(
        restaurant_id=RESTAURANT_ID, start_at=start, end_at=end, expected_covers=None
    )
    assert covers == 45
    repo.sum_expected_covers.assert_called_once()


@pytest.mark.asyncio
async def test_get_staff_availability_excludes_already_assigned():
    repo = AsyncMock(spec=StaffingRepository)
    free_id, busy_id = uuid.uuid4(), uuid.uuid4()
    free_staff = type("S", (), {"id": free_id, "name": "Free Person", "role": "server"})()
    busy_staff = type("S", (), {"id": busy_id, "name": "Busy Person", "role": "server"})()
    repo.list_active_staff.return_value = [free_staff, busy_staff]
    repo.list_assigned_staff_ids_in_window.return_value = {busy_id}
    service = StaffingService(repo, _settings())
    start, end = _window()

    available = await service.get_staff_availability(restaurant_id=RESTAURANT_ID, start_at=start, end_at=end, role=None)
    assert [s.id for s in available] == [free_id]
