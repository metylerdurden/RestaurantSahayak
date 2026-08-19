from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_first(self, restaurant_id: uuid.UUID) -> User | None:
        """No auth system exists yet (out of scope for this MVP — see User model's
        own docstring). The Manager API needs *some* user id to attribute manager-
        initiated actions (approval decisions, manager-triggered mutations) to, so
        the dashboard reads this restaurant's first manager as a stand-in "current
        user" rather than the frontend inventing/hardcoding an id."""
        stmt = (
            select(User)
            .where(User.restaurant_id == restaurant_id, User.is_active.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
