from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

WorkflowType = Literal["daily_briefing", "inventory_monitoring", "reservation_monitoring", "staffing_monitoring"]


class WorkflowRunDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_type: str
    restaurant_id: uuid.UUID
    correlation_id: uuid.UUID
    status: Literal["running", "completed", "failed"]
    triggered_by: Literal["scheduler", "manual"]
    started_at: datetime
    completed_at: datetime | None
    final_result: dict[str, Any] | None
    error: str | None


class ToolCallSummaryDTO(BaseModel):
    agent_run_id: uuid.UUID
    tool_name: str | None
    content: dict[str, Any]


class AgentRunSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_name: str
    model_name: str
    status: Literal["running", "completed", "failed"]
    outcome_summary: str | None
    started_at: datetime
    completed_at: datetime | None


class WorkflowRunDetail(BaseModel):
    """The full requested shape: the workflow run itself, plus every agent run and
    tool call it caused (found via shared correlation_id, not duplicated storage)."""

    workflow_run: WorkflowRunDTO
    agent_runs: list[AgentRunSummaryDTO]
    tool_calls: list[ToolCallSummaryDTO]
    errors: list[str]
