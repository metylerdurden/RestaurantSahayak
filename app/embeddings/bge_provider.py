"""EmbeddingProvider implementation backed by BAAI/bge-m3 via sentence-transformers.

The model name is never hard-coded here — it is passed in at construction time (see
app.embeddings.factory), sourced from Settings.embedding_model (env var
EMBEDDING_MODEL, default "BAAI/bge-m3"). Loading is lazy: the multi-GB model is only
pulled into memory on first `embed()`/`health_check()` call, not at import or app
startup, so this module can be imported cheaply everywhere it's needed.
"""

from __future__ import annotations

from app.embeddings.base import EmbeddingProvider


class BGEEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str, device: str = "cpu") -> None:
        self._model_name = model_name
        self._device = device
        self._model = None  # lazy-loaded SentenceTransformer
        self._dimension: int | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._ensure_loaded()
        assert self._dimension is not None
        return self._dimension

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        # Imported lazily so importing app.embeddings doesn't require torch to be
        # importable unless an embedding is actually requested.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self._model_name, device=self._device)
        get_dimension = getattr(
            self._model, "get_embedding_dimension", self._model.get_sentence_embedding_dimension
        )
        self._dimension = get_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        assert self._model is not None
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def health_check(self) -> bool:
        try:
            vectors = self.embed(["dineops embedding provider health check"])
        except Exception:
            return False
        return len(vectors) == 1 and len(vectors[0]) == self.dimension
