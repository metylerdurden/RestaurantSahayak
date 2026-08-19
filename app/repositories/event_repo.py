from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import Event
from app.repositories.base import BaseRepository


class EventRepository(BaseRepository):
    async def create(self, **fields) -> Event:
        event = Event(**fields)
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_id(self, event_id: uuid.UUID) -> Event | None:
        return await self.session.get(Event, event_id)

    async def get_by_idempotency_key(self, restaurant_id: uuid.UUID, idempotency_key: str) -> Event | None:
        stmt = select(Event).where(
            Event.restaurant_id == restaurant_id, Event.idempotency_key == idempotency_key
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_unhandled(self, restaurant_id: uuid.UUID) -> list[Event]:
        stmt = (
            select(Event)
            .where(Event.restaurant_id == restaurant_id, Event.handled.is_(False))
            .order_by(Event.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def save(self, event: Event) -> Event:
        await self.session.flush()
        return event
