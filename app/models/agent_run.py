from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow

AGENT_NAMES = ("orchestrator", "reservation", "inventory", "customer", "staffing", "analytics")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('manager_request','event')", name="ck_agent_runs_trigger_type_valid"
        ),
        CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_agent_runs_status_valid"
        ),
        CheckConstraint(
            "(trigger_type = 'manager_request' AND initiated_by_user_id IS NOT NULL "
            "AND triggering_event_id IS NULL) OR "
            "(trigger_type = 'event' AND triggering_event_id IS NOT NULL "
            "AND initiated_by_user_id IS NULL)",
            name="ck_agent_runs_trigger_consistency",
        ),
        Index("ix_agent_runs_restaurant_agent_started", "restaurant_id", "agent_name", "started_at"),
        Index("ix_agent_runs_correlation_id", "correlation_id"),
        Index("ix_agent_runs_parent_run_id", "parent_run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String, nullable=False)
    initiated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    triggering_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    outcome_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','tool_call','tool_result','system')",
            name="ck_agent_messages_role_valid",
        ),
        Index("uq_agent_messages_run_sequence", "run_id", "sequence_number", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
