"""MemoryService subsystem — implemented starting Phase 6.

Reached by agents only through app.tools.memory_tools (added in Phase 6), never
called directly by other domain services. Memory recall is structured lookup by
scope/type/topic; any future semantic-embedding-based recall (via
app.embeddings.EmbeddingProvider) still operates over DineOps' own structured Memory
records, not documents — it is not RAG (see Constitution II / research.md).
"""
