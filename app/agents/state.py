"""Typed state and result schemas for the Reservation Agent's tool-calling loop.

Kept separate from reservation_agent.py so the shapes an agent communicates in are
visible without reading the control flow. Pure Pydantic models — no sqlalchemy,
app.models, or app.repositories imports (enforced by test_layer_boundaries.py).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    """One tool invocation the agent made during a run, and what it got back — the
    audit trail requested for agent runs: which tools, what inputs, what results."""

    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any]


class AgentErrorInfo(BaseModel):
    code: str
    message: str


class AgentState(BaseModel):
    """Mutable state threaded through one ReservationAgent.handle() loop, iteration
    to iteration. Not persisted directly — AgentRunService persists an AgentMessage
    per turn instead; this is the in-memory working state of a single run."""

    task: str
    iterations: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    final_content: str | None = None
    error: AgentErrorInfo | None = None


class ReservationAgentResult(BaseModel):
    """Structured result returned to the caller of ReservationAgent.handle().
    `data` carries the JSON-serialized payload of the last successful tool call (a
    reservation, a list of reservations, available-table options, ...) so callers can
    act on specifics without re-parsing `summary`."""

    agent_run_id: uuid.UUID
    status: Literal["completed", "pending_approval", "error"]
    summary: str
    tool_calls: list[ToolCallRecord]
    data: dict[str, Any] | None = None
    error: AgentErrorInfo | None = None
    latency_ms: int
