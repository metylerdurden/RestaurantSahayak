from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.models import Reservation, Staff, StaffShift, ShiftAssignment
from app.repositories.base import BaseRepository

ACTIVE_RESERVATION_STATUSES = ("requested", "booked", "modified", "completed")


class StaffingRepository(BaseRepository):
    async def list_shifts(
        self, restaurant_id: uuid.UUID, date_from: datetime, date_to: datetime
    ) -> list[StaffShift]:
        stmt = (
            select(StaffShift)
            .where(
                StaffShift.restaurant_id == restaurant_id,
                StaffShift.start_at >= date_from,
                StaffShift.start_at <= date_to,
            )
            .order_by(StaffShift.start_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_assignments_for_shifts(
        self, shift_ids: list[uuid.UUID]
    ) -> list[tuple[ShiftAssignment, Staff]]:
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

    async def list_active_staff(
        self, restaurant_id: uuid.UUID, *, role: str | None = None
    ) -> list[Staff]:
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

    async def sum_expected_covers(
        self, restaurant_id: uuid.UUID, start_at: datetime, end_at: datetime
    ) -> int:
        stmt = select(Reservation.party_size).where(
            Reservation.restaurant_id == restaurant_id,
            Reservation.requested_time >= start_at,
            Reservation.requested_time < end_at,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return sum(rows)
