from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, UniqueConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class User(Base, TimestampMixin):
    """A manager. Table name `users` — `User` collides with SQL keywords.

    Note: uniqueness on (restaurant_id, email) is case-sensitive for MVP simplicity
    (a functional lower(email) unique index is the natural upgrade if case-insensitive
    login matching becomes a real requirement).
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role = 'manager'", name="ck_users_role_manager_only"),
        UniqueConstraint("restaurant_id", "email", name="uq_users_restaurant_email"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="manager")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
