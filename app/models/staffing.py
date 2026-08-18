from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk, _utcnow


class StaffShift(Base, TimestampMixin):
    __tablename__ = "staff_shifts"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_staff_shifts_end_after_start"),
        CheckConstraint(
            "required_staff_count > 0", name="ck_staff_shifts_required_count_positive"
        ),
        CheckConstraint(
            "status IN ('staffed','understaffed')", name="ck_staff_shifts_status_valid"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    required_staff_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="understaffed")


class ShiftAssignment(Base):
    """Join table between Staff and StaffShift — not one of the originally named
    entities but structurally required for the many-to-many relationship."""

    __tablename__ = "shift_assignments"
    __table_args__ = (
        UniqueConstraint("shift_id", "staff_id", name="uq_shift_assignments_shift_staff"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_shifts.id", ondelete="CASCADE"), nullable=False
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id", ondelete="RESTRICT"), nullable=False
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
