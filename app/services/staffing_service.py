"""Staffing domain business rules.

calculate_staff_requirement's formula is an explicit, documented business rule (not a
black box): `required_servers = max(minimum_servers, ceil(covers / covers_per_server))`,
same shape for cooks, plus a fixed 1-host baseline. Defaults live in Settings
(covers_per_server, covers_per_cook, minimum_servers_per_shift, minimum_cooks_per_shift)
so they're configurable without a code change.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime

from app.core.config import Settings
from app.models import Staff, StaffShift, ShiftAssignment
from app.repositories.staffing_repo import StaffingRepository


class StaffingService:
    def __init__(self, repo: StaffingRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    async def get_staff_schedule(
        self, *, restaurant_id: uuid.UUID, date_from: datetime, date_to: datetime
    ) -> list[tuple[StaffShift, list[tuple[Staff, ShiftAssignment]]]]:
        shifts = await self.repo.list_shifts(restaurant_id, date_from, date_to)
        assignment_rows = await self.repo.list_assignments_for_shifts([s.id for s in shifts])

        by_shift: dict[uuid.UUID, list[tuple[Staff, ShiftAssignment]]] = defaultdict(list)
        for assignment, staff in assignment_rows:
            by_shift[assignment.shift_id].append((staff, assignment))

        return [(shift, by_shift.get(shift.id, [])) for shift in shifts]

    async def get_staff_availability(
        self,
        *,
        restaurant_id: uuid.UUID,
        start_at: datetime,
        end_at: datetime,
        role: str | None,
    ) -> list[Staff]:
        all_staff = await self.repo.list_active_staff(restaurant_id, role=role)
        assigned_ids = await self.repo.list_assigned_staff_ids_in_window(
            restaurant_id, start_at, end_at
        )
        return [s for s in all_staff if s.id not in assigned_ids]

    async def calculate_staff_requirement(
        self,
        *,
        restaurant_id: uuid.UUID,
        start_at: datetime,
        end_at: datetime,
        expected_covers: int | None,
    ) -> tuple[int, int, int, int, int]:
        if expected_covers is None:
            expected_covers = await self.repo.sum_expected_covers(restaurant_id, start_at, end_at)

        required_servers = max(
            self.settings.minimum_servers_per_shift,
            math.ceil(expected_covers / self.settings.covers_per_server),
        )
        required_cooks = max(
            self.settings.minimum_cooks_per_shift,
            math.ceil(expected_covers / self.settings.covers_per_cook),
        )
        required_host = 1
        required_total = required_servers + required_cooks + required_host

        return expected_covers, required_servers, required_cooks, required_host, required_total
