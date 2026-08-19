"""WorkflowRun: the durable record of one autonomous/background workflow execution
(Step 17) — one level above AgentRun. A background workflow (the daily briefing,
inventory monitoring, ...) typically invokes one or more agents (directly, or via the
Orchestrator, which itself spawns further AgentRuns); every one of those AgentRuns
shares this WorkflowRun's `correlation_id`, so "which agent runs and tool calls did
this workflow execution cause" is answered by joining
agent_runs/agent_messages WHERE correlation_id = workflow_runs.correlation_id rather
than duplicating that tracking here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow

WORKFLOW_TYPES = (
    "daily_briefing",
    "inventory_monitoring",
    "reservation_monitoring",
    "staffing_monitoring",
)
WORKFLOW_RUN_STATUSES = ("running", "completed", "failed")
WORKFLOW_TRIGGERED_BY = ("scheduler", "manual")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "workflow_type IN ("
            "'daily_briefing','inventory_monitoring',"
            "'reservation_monitoring','staffing_monitoring')",
            name="ck_workflow_runs_workflow_type_valid",
        ),
        CheckConstraint(
            "status IN ('running','completed','failed')", name="ck_workflow_runs_status_valid"
        ),
        CheckConstraint(
            "triggered_by IN ('scheduler','manual')", name="ck_workflow_runs_triggered_by_valid"
        ),
        Index("ix_workflow_runs_restaurant_type_started", "restaurant_id", "workflow_type", "started_at"),
        Index("ix_workflow_runs_correlation_id", "correlation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()  # == workflow_id
    workflow_type: Mapped[str] = mapped_column(String, nullable=False)
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    triggered_by: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
