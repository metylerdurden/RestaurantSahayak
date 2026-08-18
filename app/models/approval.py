from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow

APPROVAL_DOMAINS = ("reservation", "inventory", "staffing", "purchase")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "domain IN ('reservation','inventory','staffing','purchase')",
            name="ck_approvals_domain_valid",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="ck_approvals_status_valid",
        ),
        CheckConstraint(
            "(status IN ('approved','rejected') AND decided_by_user_id IS NOT NULL "
            "AND decided_at IS NOT NULL) OR (status IN ('pending','expired'))",
            name="ck_approvals_decision_consistency",
        ),
        Index("ix_approvals_restaurant_status", "restaurant_id", "status"),
        Index("ix_approvals_proposed_by_agent_run", "proposed_by_agent_run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String, nullable=False)
    proposed_by_tool: Mapped[str] = mapped_column(String, nullable=False)
    proposed_by_agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False
    )
    proposed_action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
