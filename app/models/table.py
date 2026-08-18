from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Table(Base, TimestampMixin):
    """A bookable table. Physical table name `restaurant_tables` — `table` is a
    reserved word."""

    __tablename__ = "restaurant_tables"
    __table_args__ = (
        CheckConstraint("seat_capacity > 0", name="ck_restaurant_tables_seat_capacity_positive"),
        Index(
            "uq_restaurant_tables_restaurant_label_active",
            "restaurant_id",
            "label",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    seat_capacity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
