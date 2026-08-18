"""Embedding provider abstraction — semantic embedding for MemoryService only.

NOT the agent reasoning model, and NOT RAG: there is no document ingestion, chunking,
or document-retrieval pipeline in DineOps (Constitution II). This interface exists so
MemoryService (Phase 6) can, if it chooses, do embedding-based similarity search over
DineOps' own structured Memory records — a narrower concern than RAG, and one Phase 6
may or may not actually turn on. Phase 1 only establishes the abstraction and proves
the model loads and produces embeddings.
"""

from app.embeddings.base import EmbeddingProvider
from app.embeddings.factory import get_embedding_provider

__all__ = ["EmbeddingProvider", "get_embedding_provider"]
