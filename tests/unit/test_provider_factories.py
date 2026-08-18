import pytest

from app.core.config import Settings
from app.embeddings.bge_provider import BGEEmbeddingProvider
from app.embeddings.factory import build_embedding_provider
from app.llm.factory import build_llm_provider
from app.llm.ollama_provider import OllamaLLMProvider


def _settings(**overrides) -> Settings:
    return Settings(database_url="postgresql+asyncpg://u:p@localhost:5432/db", **overrides)


def test_build_llm_provider_dispatches_to_ollama():
    provider = build_llm_provider(_settings(llm_provider="ollama", llm_model="qwen3:8b"))
    assert isinstance(provider, OllamaLLMProvider)
    assert provider.model_name == "qwen3:8b"


def test_build_llm_provider_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_llm_provider(_settings(llm_provider="does-not-exist"))


def test_build_embedding_provider_dispatches_to_bge():
    provider = build_embedding_provider(_settings(embedding_provider="bge", embedding_model="BAAI/bge-m3"))
    assert isinstance(provider, BGEEmbeddingProvider)
    assert provider.model_name == "BAAI/bge-m3"


def test_build_embedding_provider_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown EMBEDDING_PROVIDER"):
        build_embedding_provider(_settings(embedding_provider="does-not-exist"))
