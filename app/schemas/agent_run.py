"""Schemas for the Manager API's Agent Activity surface (Step 19) — triggering an
agent/orchestrator run directly from the dashboard, listing recent runs, and
assembling the full nested trace: Orchestrator -> Specialist -> Tool -> Memory ->
Result. `AgentRunNode.children` is what makes the tree — a specialist run's
`parent_run_id` pointing at the orchestrator run that delegated to it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentTarget = Literal["orchestrator", "reservation", "customer", "inventory", "staffing", "analytics"]


class AgentMessageDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence_number: int
    role: Literal["user", "assistant", "tool_call", "tool_result", "system"]
    tool_name: str | None
    content: dict[str, Any]
    created_at: datetime


class AgentRunDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    restaurant_id: uuid.UUID
    agent_name: str
    model_name: str
    parent_run_id: uuid.UUID | None
    correlation_id: uuid.UUID
    trigger_type: Literal["manager_request", "event", "scheduled"]
    status: Literal["running", "completed", "failed"]
    outcome_summary: str | None
    started_at: datetime
    completed_at: datetime | None


class AgentRunNode(BaseModel):
    """One run in a trace tree — the run itself plus every message it logged
    (including tool_result messages, which is how a memory operation like
    `search_memory`/`add_memory` shows up: a tool_result message whose tool_name
    names it) and, recursively, every specialist run it delegated to."""

    id: uuid.UUID
    agent_name: str
    model_name: str
    status: Literal["running", "completed", "failed"]
    trigger_type: Literal["manager_request", "event", "scheduled"]
    outcome_summary: str | None
    started_at: datetime
    completed_at: datetime | None
    messages: list[AgentMessageDTO]
    children: list["AgentRunNode"] = Field(default_factory=list)


AgentRunNode.model_rebuild()


class TriggerAgentRunInput(BaseModel):
    restaurant_id: uuid.UUID
    agent_name: AgentTarget = "orchestrator"
    task: str
    initiated_by_user_id: uuid.UUID | None = None


class TriggerAgentRunOutput(BaseModel):
    agent_run_id: uuid.UUID
    status: Literal["completed", "pending_approval", "error"]
    summary: str
    pending_approval_id: uuid.UUID | None = None
