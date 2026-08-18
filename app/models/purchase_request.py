from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

PURCHASE_REQUEST_STATUSES = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "fulfilled",
    "cancelled",
)


class PurchaseRequest(Base, TimestampMixin):
    """Intent to restock — distinct from InventoryTransaction (the ledger entry once
    stock actually changes). This is what goes through Approval."""

    __tablename__ = "purchase_requests"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_purchase_requests_quantity_positive"),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="ck_purchase_requests_cost_non_negative",
        ),
        CheckConstraint(
            "status IN ('draft','pending_approval','approved','rejected','fulfilled','cancelled')",
            name="ck_purchase_requests_status_valid",
        ),
        Index("ix_purchase_requests_restaurant_status", "restaurant_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False
    )
    requested_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
