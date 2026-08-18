from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Index, Numeric, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow


class Sale(Base):
    """One row per menu item sold (line-item level) — see data-model.md's modeling
    decision. No separate order/check entity for MVP."""

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_sales_unit_price_non_negative"),
        Index("ix_sales_restaurant_sold_at", "restaurant_id", "sold_at"),
        Index("ix_sales_menu_item_sold_at", "menu_item_id", "sold_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    total_price: Mapped[float] = mapped_column(
        Numeric(10, 2), Computed("quantity * unit_price", persisted=True)
    )
    sold_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
