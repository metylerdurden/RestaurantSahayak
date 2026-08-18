"""Selects and constructs the configured LLMProvider.

Adding a new provider means adding a branch here (and a new class in this package) —
callers (agents, health checks) only ever depend on the LLMProvider interface.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.ollama_provider import OllamaLLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "ollama":
        return OllamaLLMProvider(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")


@lru_cache
def get_llm_provider() -> LLMProvider:
    return build_llm_provider(get_settings())
