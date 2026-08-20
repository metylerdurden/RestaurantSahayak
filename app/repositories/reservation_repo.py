from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select

from app.models import Reservation, Table
from app.repositories.base import BaseRepository

ACTIVE_RESERVATION_STATUSES = ("requested", "booked", "modified")


class ReservationRepository(BaseRepository):
    async def get_by_id(self, reservation_id: uuid.UUID) -> Reservation | None:
        return await self.session.get(Reservation, reservation_id)

    async def get_table(self, table_id: uuid.UUID) -> Table | None:
        return await self.session.get(Table, table_id)

    async def list(
        self,
        restaurant_id: uuid.UUID,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        status: str | None = None,
        customer_id: uuid.UUID | None = None,
    ) -> list[Reservation]:
        stmt = select(Reservation).where(Reservation.restaurant_id == restaurant_id)
        if date_from is not None:
            stmt = stmt.where(Reservation.requested_time >= date_from)
        if date_to is not None:
            stmt = stmt.where(Reservation.requested_time <= date_to)
        if status is not None:
            stmt = stmt.where(Reservation.status == status)
        if customer_id is not None:
            stmt = stmt.where(Reservation.customer_id == customer_id)
        stmt = stmt.order_by(Reservation.requested_time)
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, **fields) -> Reservation:
        reservation = Reservation(**fields)
        self.session.add(reservation)
        await self.session.flush()
        return reservation

    async def save(self, reservation: Reservation) -> Reservation:
        await self.session.flush()
        return reservation

    async def table_has_conflict(
        self,
        table_id: uuid.UUID,
        requested_time: datetime,
        duration_minutes: int,
        *,
        exclude_reservation_id: uuid.UUID | None = None,
    ) -> bool:
        end_time = requested_time + timedelta(minutes=duration_minutes)
        stmt = select(Reservation).where(
            Reservation.table_id == table_id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
        if exclude_reservation_id is not None:
            stmt = stmt.where(Reservation.id != exclude_reservation_id)
        existing = (await self.session.execute(stmt)).scalars().all()
        for other in existing:
            other_end = other.requested_time + timedelta(minutes=other.duration_minutes)
            if other.requested_time < end_time and requested_time < other_end:
                return True
        return False

    async def find_available_tables(
        self,
        restaurant_id: uuid.UUID,
        party_size: int,
        requested_time: datetime,
        duration_minutes: int,
    ) -> List[Table]:  # typing.List, not the builtin: `list` is shadowed within this
        # class's own body by the `list()` method defined above, once it's fully
        # bound in the class namespace — a real (if obscure) Python scoping gotcha,
        # not a typo.
        stmt = (
            select(Table)
            .where(
                Table.restaurant_id == restaurant_id,
                Table.is_active.is_(True),
                Table.seat_capacity >= party_size,
            )
            .order_by(Table.seat_capacity.asc())
        )
        candidates = (await self.session.execute(stmt)).scalars().all()
        available = []
        for table in candidates:
            if not await self.table_has_conflict(table.id, requested_time, duration_minutes):
                available.append(table)
        return available
