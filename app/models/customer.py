from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "phone IS NOT NULL OR email IS NOT NULL", name="ck_customers_has_contact_method"
        ),
        UniqueConstraint("restaurant_id", "phone", name="uq_customers_restaurant_phone"),
        UniqueConstraint("restaurant_id", "email", name="uq_customers_restaurant_email"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
