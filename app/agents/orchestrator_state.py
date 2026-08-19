"""Typed state and result schemas for OrchestratorAgent.

Two families of types here:
- The LangGraph state (`OrchestratorGraphState`, a TypedDict — LangGraph's
  StateGraph works on plain mappings with reducers, not Pydantic models) that flows
  between graph nodes and accumulates as the orchestrator delegates to specialists.
- The typed decisions the orchestrator's own LLM calls produce (`DelegateDecision`,
  `FinishDecision`) and the typed result handed back to the orchestrator's caller
  (`OrchestratorResult`, `SpecialistInvocationRecord`) — Pydantic, like every other
  agent's result type, for validation and JSON-safety.

No sqlalchemy/app.models/app.repositories imports — this module lives in app.agents
and test_layer_boundaries.py enforces that.
"""

from __future__ import annotations

import operator
import uuid
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

SPECIALIST_NAMES = ("reservation", "customer", "inventory", "staffing", "analytics")
SpecialistName = Literal["reservation", "customer", "inventory", "staffing", "analytics"]


# --- the orchestrator's own routing decisions (validated LLM tool-call output) ---


class DelegateDecision(BaseModel):
    agent_name: SpecialistName
    instruction: str = Field(description="The specific task to give that specialist agent.")


class FinishDecision(BaseModel):
    pass


# --- LangGraph state ---


class SpecialistInvocationState(TypedDict):
    agent_name: str
    task: str
    result: dict[str, Any]  # AgentResult.model_dump(mode="json")
    attempt: int


class OrchestratorGraphState(TypedDict):
    task: str
    restaurant_id: str
    trigger_type: str
    initiated_by_user_id: str | None
    triggering_event_id: str | None
    orchestrator_run_id: str
    correlation_id: str
    invocations: Annotated[list[SpecialistInvocationState], operator.add]
    remaining_steps: int
    next_agent: str | None
    next_instruction: str | None
    finished: bool
    final_response: str | None
    error: str | None
    # Set by _delegate_node when a specialist comes back pending_approval; consumed
    # by _await_approval_node, which LangGraph pauses at (interrupt()) until a human
    # decision resumes the graph via OrchestratorAgent.resume().
    pending_approval_id: str | None
    pending_approval_agent: str | None


# --- public result types ---


class SpecialistInvocationRecord(BaseModel):
    agent_name: str
    task: str
    status: Literal["completed", "pending_approval", "error"]
    summary: str
    data: dict[str, Any] | None = None


class OrchestratorResult(BaseModel):
    orchestrator_run_id: uuid.UUID
    status: Literal["completed", "pending_approval", "error"]
    summary: str
    invocations: list[SpecialistInvocationRecord]
    pending_approvals: list[SpecialistInvocationRecord] = Field(default_factory=list)
    latency_ms: int
    error: str | None = None
    # Set only when status == "pending_approval": the approval that paused the
    # workflow, and the thread_id (== orchestrator_run_id, as a string) that must be
    # passed back into OrchestratorAgent.resume() to continue it.
    awaiting_approval_id: uuid.UUID | None = None
