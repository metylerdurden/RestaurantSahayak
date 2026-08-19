from __future__ import annotations

from sqlalchemy import select

from app.models import Restaurant
from app.repositories.base import BaseRepository


class RestaurantRepository(BaseRepository):
    async def list_all(self) -> list[Restaurant]:
        stmt = select(Restaurant).where(Restaurant.is_active.is_(True)).order_by(Restaurant.name)
        return list((await self.session.execute(stmt)).scalars().all())
