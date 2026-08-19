"""Abstract interface every LLM provider implements.

Agents (Phase 4+) depend only on this interface, never on a concrete provider class or
on any provider-specific SDK — swapping Ollama/Qwen3-8B for another provider/model later
means writing a new class here, not touching agent code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A single function call the model asked for. `id` is synthesized by the
    provider (Ollama's chat API does not assign one) so a caller can correlate a
    tool's result back to the call that produced it within one turn."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    """Set on an assistant message being replayed into history after it requested
    tool calls — lets the model see its own prior call when reasoning about results."""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Agent reasoning / tool selection / structured decisions. Never used for
    embeddings — see app.embeddings.EmbeddingProvider for that concern."""

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Produce a single completion for the given conversation. `tools`, when
        given, is a list of OpenAI/Ollama-style function schemas (see
        `app.tools.base.Tool.to_llm_schema`) — the model may respond with
        `LLMResponse.tool_calls` instead of (or alongside) `content`."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Cheap connectivity + model-availability check (no full generation)."""
