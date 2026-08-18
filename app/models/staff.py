from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

STAFF_ROLES = ("server", "cook", "host", "bartender", "manager_on_duty", "other")


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"
    __table_args__ = (
        CheckConstraint(
            "role IN ('server','cook','host','bartender','manager_on_duty','other')",
            name="ck_staff_role_valid",
        ),
        Index("ix_staff_restaurant_active", "restaurant_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    contact_info: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
