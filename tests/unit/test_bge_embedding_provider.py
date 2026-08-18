"""Unit tests for BGEEmbeddingProvider using a fake SentenceTransformer — no model
download, no network. Real model loading is proven separately by
scripts/verify_providers.py (slow: downloads/loads BAAI/bge-m3)."""

from __future__ import annotations

import sentence_transformers

from app.embeddings.bge_provider import BGEEmbeddingProvider


class _FakeSentenceTransformer:
    def __init__(self, model_name: str, device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device

    def get_sentence_embedding_dimension(self) -> int:
        return 4

    def encode(self, texts, normalize_embeddings: bool = True):  # noqa: ANN001, ARG002
        import numpy as np

        return np.array([[float(len(t))] * 4 for t in texts])


def test_model_not_loaded_until_first_use(monkeypatch):
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _FakeSentenceTransformer)
    provider = BGEEmbeddingProvider(model_name="BAAI/bge-m3")
    assert provider._model is None  # not loaded at construction


def test_embed_lazily_loads_and_returns_vectors(monkeypatch):
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _FakeSentenceTransformer)
    provider = BGEEmbeddingProvider(model_name="BAAI/bge-m3")

    vectors = provider.embed(["hello", "hi"])

    assert len(vectors) == 2
    assert all(len(v) == provider.dimension for v in vectors)
    assert provider._model is not None  # now loaded


def test_health_check_true_on_successful_embed(monkeypatch):
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _FakeSentenceTransformer)
    provider = BGEEmbeddingProvider(model_name="BAAI/bge-m3")
    assert provider.health_check() is True


def test_health_check_false_on_load_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("model not found")

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _raise)
    provider = BGEEmbeddingProvider(model_name="nonexistent/model")
    assert provider.health_check() is False


def test_model_name_is_never_hardcoded_in_provider():
    provider = BGEEmbeddingProvider(model_name="some-other-org/some-other-model")
    assert provider.model_name == "some-other-org/some-other-model"
