"""Approval: the human-approval gate (Constitution IV). An agent proposes a
consequential action instead of performing it directly; a manager approves or
rejects it; only an approval triggers execution. This is the ONLY path by which a
gated action ever takes effect — a tool that requires approval never mutates
anything itself when it decides an action is gated (see e.g.
ReservationService.cancel_reservation), so there is no code path for an agent to
bypass this by "just calling the tool again."
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow

APPROVAL_DOMAINS = ("reservation", "inventory", "staffing", "purchase")
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")
APPROVAL_RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")


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
            "risk_level IN ('LOW','MEDIUM','HIGH')",
            name="ck_approvals_risk_level_valid",
        ),
        CheckConstraint(
            "(status = 'approved' AND approved_at IS NOT NULL AND decided_by_user_id IS NOT NULL "
            "AND rejected_at IS NULL) "
            "OR (status = 'rejected' AND rejected_at IS NOT NULL AND decided_by_user_id IS NOT NULL "
            "AND approved_at IS NULL) "
            "OR (status IN ('pending','expired') AND approved_at IS NULL AND rejected_at IS NULL)",
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

    # --- what was proposed, by whom, and why ---
    action: Mapped[str] = mapped_column(String, nullable=False)  # the tool name, e.g. "cancel_reservation"
    agent_name: Mapped[str] = mapped_column(String, nullable=False)  # denormalized from AgentRun for direct queries
    proposed_by_agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)  # human-readable explanation
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)  # everything needed to execute if approved
    risk_level: Mapped[str] = mapped_column(String, nullable=False)

    # --- decision ---
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    # --- execution, once approved ---
    execution_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
