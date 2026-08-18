from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk

RESERVATION_STATUSES = ("requested", "booked", "modified", "cancelled", "completed", "no_show")


class Reservation(Base, TimestampMixin):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("party_size > 0", name="ck_reservations_party_size_positive"),
        CheckConstraint("duration_minutes > 0", name="ck_reservations_duration_positive"),
        CheckConstraint(
            "status IN ('requested','booked','modified','cancelled','completed','no_show')",
            name="ck_reservations_status_valid",
        ),
        CheckConstraint(
            "created_via IN ('manager_request','event')", name="ck_reservations_created_via_valid"
        ),
        Index("ix_reservations_restaurant_requested_time", "restaurant_id", "requested_time"),
        Index("ix_reservations_table_requested_time", "table_id", "requested_time"),
        Index("ix_reservations_customer", "customer_id"),
        Index(
            "ix_reservations_active_by_restaurant",
            "restaurant_id",
            "status",
            postgresql_where=text("status IN ('requested','booked','modified')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurant_tables.id", ondelete="SET NULL"), nullable=True
    )
    party_size: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    requested_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=90)
    status: Mapped[str] = mapped_column(String, nullable=False, default="requested")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_via: Mapped[str] = mapped_column(String, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
