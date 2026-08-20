from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import select

from app.models import Reservation, ShiftAssignment, Staff, StaffShift
from app.repositories.base import BaseRepository

ACTIVE_RESERVATION_STATUSES = ("requested", "booked", "modified", "completed")


class StaffingRepository(BaseRepository):
    async def list_shifts(self, restaurant_id: uuid.UUID, date_from: datetime, date_to: datetime) -> list[StaffShift]:
        # date_from/date_to name whole calendar days, not precise instants — a caller
        # (an LLM in particular) reasonably passes a bare date like "2026-08-20" for
        # "shifts on this day", which parses to that day's midnight. Comparing
        # start_at against that midnight directly would silently exclude every shift
        # later in the day. Normalize to the full [start of date_from's day, end of
        # date_to's day] range regardless of what time-of-day was actually supplied.
        day_start = datetime.combine(date_from.date(), time.min, tzinfo=date_from.tzinfo)
        day_end = datetime.combine(date_to.date(), time.max, tzinfo=date_to.tzinfo)
        stmt = (
            select(StaffShift)
            .where(
                StaffShift.restaurant_id == restaurant_id,
                StaffShift.start_at >= day_start,
                StaffShift.start_at <= day_end,
            )
            .order_by(StaffShift.start_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_assignments_for_shifts(self, shift_ids: list[uuid.UUID]) -> list[tuple[ShiftAssignment, Staff]]:
        """No ORM relationships exist between ShiftAssignment/Staff/StaffShift (Phase
        2's models are plain FK columns only) — assemble the join explicitly here
        rather than adding relationship() attributes to already-migrated models."""
        if not shift_ids:
            return []
        stmt = (
            select(ShiftAssignment, Staff)
            .join(Staff, ShiftAssignment.staff_id == Staff.id)
            .where(ShiftAssignment.shift_id.in_(shift_ids))
        )
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]

    async def list_active_staff(self, restaurant_id: uuid.UUID, *, role: str | None = None) -> list[Staff]:
        stmt = select(Staff).where(Staff.restaurant_id == restaurant_id, Staff.is_active.is_(True))
        if role is not None:
            stmt = stmt.where(Staff.role == role)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_assigned_staff_ids_in_window(
        self, restaurant_id: uuid.UUID, start_at: datetime, end_at: datetime
    ) -> set[uuid.UUID]:
        stmt = (
            select(ShiftAssignment.staff_id)
            .join(StaffShift, ShiftAssignment.shift_id == StaffShift.id)
            .where(
                StaffShift.restaurant_id == restaurant_id,
                StaffShift.start_at < end_at,
                StaffShift.end_at > start_at,
            )
        )
        return {row[0] for row in (await self.session.execute(stmt)).all()}

    async def sum_expected_covers(self, restaurant_id: uuid.UUID, start_at: datetime, end_at: datetime) -> int:
        stmt = select(Reservation.party_size).where(
            Reservation.restaurant_id == restaurant_id,
            Reservation.requested_time >= start_at,
            Reservation.requested_time < end_at,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return sum(rows)
