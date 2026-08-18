"""Selects and constructs the configured EmbeddingProvider."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.bge_provider import BGEEmbeddingProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "bge":
        return BGEEmbeddingProvider(
            model_name=settings.embedding_model,
            device=settings.embedding_device,
        )
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider!r}")


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return build_embedding_provider(get_settings())
