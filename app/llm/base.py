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
class LLMMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
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
        **kwargs: Any,
    ) -> LLMResponse:
        """Produce a single completion for the given conversation."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Cheap connectivity + model-availability check (no full generation)."""
