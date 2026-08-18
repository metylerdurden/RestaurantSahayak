from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk, _utcnow

INVENTORY_STATUSES = ("ok", "low", "out_of_stock")
INVENTORY_CHANGE_TYPES = ("delivery", "waste", "correction", "purchase_received", "other")


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="ck_inventory_items_quantity_non_negative"),
        CheckConstraint(
            "low_stock_threshold >= 0", name="ck_inventory_items_threshold_non_negative"
        ),
        CheckConstraint(
            "status IN ('ok','low','out_of_stock')", name="ck_inventory_items_status_valid"
        ),
        UniqueConstraint("restaurant_id", "name", name="uq_inventory_items_restaurant_name"),
        Index("ix_inventory_items_restaurant_status", "restaurant_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    quantity_on_hand: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    low_stock_threshold: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ok")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('delivery','waste','correction','purchase_received','other')",
            name="ck_inventory_transactions_change_type_valid",
        ),
        CheckConstraint("quantity_delta <> 0", name="ck_inventory_transactions_delta_nonzero"),
        CheckConstraint(
            "resulting_quantity >= 0", name="ck_inventory_transactions_resulting_non_negative"
        ),
        Index(
            "ix_inventory_transactions_restaurant_item_recorded",
            "restaurant_id",
            "item_id",
            "recorded_at",
        ),
        Index(
            "ix_inventory_transactions_purchase_request",
            "purchase_request_id",
            postgresql_where=text("purchase_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    quantity_delta: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    resulting_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="SET NULL"), nullable=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    recorded_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
