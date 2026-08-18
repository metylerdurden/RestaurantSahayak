"""LLM provider abstraction: agent reasoning, tool selection, structured decisions.

This is the ONLY model concern used for agent reasoning. It must never be used for
memory embedding/search — that is app.embeddings' job.
"""

from app.llm.base import LLMMessage, LLMProvider, LLMResponse
from app.llm.factory import get_llm_provider

__all__ = ["LLMMessage", "LLMProvider", "LLMResponse", "get_llm_provider"]
