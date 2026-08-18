"""Read-only aggregate queries across Sale/Reservation/MenuItem — Analytics has no
owned tables. Still the only layer touching SQL for this domain (see
research.md §7 in the architecture docs for why Analytics reads other domains'
tables directly instead of routing through their services)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.models import MenuItem, Reservation, Sale
from app.repositories.base import BaseRepository

COMPLETED_OR_NO_SHOW = ("completed", "no_show")


class AnalyticsRepository(BaseRepository):
    async def daily_sales(
        self, restaurant_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[tuple]:
        stmt = (
            select(
                func.date(Sale.sold_at).label("sales_date"),
                func.sum(Sale.total_price).label("revenue"),
                func.sum(Sale.quantity).label("items_sold"),
            )
            .where(Sale.restaurant_id == restaurant_id, Sale.sold_at >= start, Sale.sold_at < end)
            .group_by(func.date(Sale.sold_at))
            .order_by(func.date(Sale.sold_at))
        )
        return list((await self.session.execute(stmt)).all())

    async def covers_by_date(
        self, restaurant_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict:
        stmt = (
            select(func.date(Reservation.requested_time), func.sum(Reservation.party_size))
            .where(
                Reservation.restaurant_id == restaurant_id,
                Reservation.requested_time >= start,
                Reservation.requested_time < end,
                Reservation.status == "completed",
            )
            .group_by(func.date(Reservation.requested_time))
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def item_sales(
        self, restaurant_id: uuid.UUID, start: datetime, end: datetime, limit: int
    ) -> list[tuple]:
        stmt = (
            select(
                MenuItem.id,
                MenuItem.name,
                func.sum(Sale.quantity).label("quantity_sold"),
                func.sum(Sale.total_price).label("revenue"),
            )
            .join(Sale, Sale.menu_item_id == MenuItem.id)
            .where(Sale.restaurant_id == restaurant_id, Sale.sold_at >= start, Sale.sold_at < end)
            .group_by(MenuItem.id, MenuItem.name)
            .order_by(func.sum(Sale.quantity).desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).all())

    async def no_show_stats(
        self, restaurant_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict[str, int]:
        stmt = (
            select(Reservation.status, func.count())
            .where(
                Reservation.restaurant_id == restaurant_id,
                Reservation.requested_time >= start,
                Reservation.requested_time < end,
                Reservation.status.in_(COMPLETED_OR_NO_SHOW),
            )
            .group_by(Reservation.status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}
