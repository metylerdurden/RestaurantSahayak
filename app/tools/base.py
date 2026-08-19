"""Typed tool contract: the ONLY surface agents are allowed to act through.

Agent -> Typed Tool -> Domain Service -> Repository -> SQLAlchemy -> PostgreSQL

Rule (enforced by tests/unit/test_layer_boundaries.py): nothing in app.tools imports
sqlalchemy, app.models, or app.repositories. A tool holds a Service instance (never a
Repository or a database session) and does exactly three things: validate typed input,
call one service method, return typed output (or a PendingApprovalOutput when the
service determines the action is high-impact). Agents never construct or see SQL —
there is no code path from an agent to the database that does not pass through here.

`Tool.to_llm_schema()` renders the tool as an OpenAI/Ollama-style function-calling
spec (`{"type": "function", "function": {...}}`), so a Tool instance is directly usable
as an entry in the `tools=[...]` list passed to LLMProvider.generate() once agents
exist (Phase 4+) — no redesign needed then.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ToolContext(BaseModel):
    """Request-scoped identifiers threaded through a tool call, purely for
    observability/authorization — never a database session or ORM object."""

    restaurant_id: uuid.UUID
    correlation_id: str
    acting_agent: str
    trigger_type: Literal["manager_request", "event", "scheduled"] = "manager_request"
    agent_run_id: uuid.UUID | None = None


class PendingApprovalOutput(BaseModel):
    """Returned instead of a tool's normal output when the underlying service
    classifies the requested action as high-impact (Constitution IV). The action has
    NOT been performed — it becomes an Approval row and executes only once approved."""

    status: Literal["pending_approval"] = "pending_approval"
    approval_id: uuid.UUID
    summary: str


class ToolError(Exception):
    """Raised by a tool for any input/business-rule failure. Always carries a stable
    `code` (for programmatic handling) and a human-readable `message` — agents and
    callers get a clear, typed error, never a raw exception/stack trace."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ToolErrorOutput(BaseModel):
    """A ToolError rendered as data, for callers that want a typed value instead of
    a caught exception (e.g. the future Orchestrator, which should present a decline
    rather than crash the conversation)."""

    status: Literal["error"] = "error"
    code: str
    message: str

    @classmethod
    def from_error(cls, error: ToolError) -> "ToolErrorOutput":
        return cls(code=error.code, message=error.message)


class Tool(ABC, Generic[InputT, OutputT]):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]
    high_impact: ClassVar[bool] = False

    async def __call__(
        self, raw_input: dict[str, Any] | BaseModel, *, context: ToolContext
    ) -> OutputT | PendingApprovalOutput:
        validated = (
            raw_input
            if isinstance(raw_input, self.input_model)
            else self.input_model.model_validate(raw_input)
        )
        return await self.run(validated, context=context)  # type: ignore[arg-type]

    @abstractmethod
    async def run(self, input: InputT, *, context: ToolContext) -> OutputT | PendingApprovalOutput: ...

    def to_llm_schema(self) -> dict[str, Any]:
        """OpenAI/Ollama function-calling schema — pass directly in `tools=[...]`."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
