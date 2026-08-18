"""Abstract interface every embedding provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Semantic embedding for MemoryService use only — never agent reasoning."""

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector length produced by this provider/model."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Loads the underlying model on first use."""

    @abstractmethod
    def health_check(self) -> bool:
        """Attempts to load the model (if not already loaded) and embed a probe
        string. Expensive on first call — not wired into GET /health/ready."""
