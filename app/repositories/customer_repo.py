from __future__ import annotations

import uuid

from sqlalchemy import or_, select

from app.models import Customer, Reservation
from app.repositories.base import BaseRepository


class CustomerRepository(BaseRepository):
    async def get_by_id(self, customer_id: uuid.UUID) -> Customer | None:
        return await self.session.get(Customer, customer_id)

    async def list_all(self, restaurant_id: uuid.UUID, *, limit: int = 100) -> list[Customer]:
        stmt = (
            select(Customer)
            .where(Customer.restaurant_id == restaurant_id, Customer.is_active.is_(True))
            .order_by(Customer.name)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find(self, restaurant_id: uuid.UUID, query: str) -> list[Customer]:
        pattern = f"%{query}%"
        stmt = select(Customer).where(
            Customer.restaurant_id == restaurant_id,
            Customer.is_active.is_(True),
            or_(
                Customer.name.ilike(pattern),
                Customer.phone.ilike(pattern),
                Customer.email.ilike(pattern),
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_reservation_history(self, customer_id: uuid.UUID, *, limit: int = 20) -> list[Reservation]:
        stmt = (
            select(Reservation)
            .where(Reservation.customer_id == customer_id)
            .order_by(Reservation.requested_time.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def save(self, customer: Customer) -> Customer:
        await self.session.flush()
        return customer
