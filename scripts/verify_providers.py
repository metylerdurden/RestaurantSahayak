"""Manual verification: proves the configured LLMProvider can actually reach Qwen3-8B
through Ollama, and the configured EmbeddingProvider can actually load BAAI/bge-m3 and
produce embeddings. Not part of the automated test suite (the LLM call is slow-ish and
the embedding model load downloads several GB on first run) — run by hand:

    uv run python scripts/verify_providers.py
"""

from __future__ import annotations

import asyncio
import sys
import time

from app.core.config import get_settings
from app.embeddings.factory import get_embedding_provider
from app.llm.factory import get_llm_provider
from app.llm.base import LLMMessage


async def verify_llm() -> bool:
    settings = get_settings()
    provider = get_llm_provider()
    print(f"\n[LLM] provider={settings.llm_provider} model={provider.model_name} "
          f"base_url={settings.ollama_base_url}")

    print("[LLM] health_check() ...", end=" ", flush=True)
    healthy = await provider.health_check()
    print("OK" if healthy else "FAILED")
    if not healthy:
        print(f"[LLM] Model {provider.model_name!r} not reachable/pulled in Ollama.")
        return False

    print("[LLM] generate() a real completion ...", end=" ", flush=True)
    start = time.monotonic()
    response = await provider.generate(
        [LLMMessage(role="user", content="Reply with exactly the word: pong")]
    )
    elapsed = time.monotonic() - start
    print(f"OK ({elapsed:.1f}s)")
    print(f"[LLM] model={response.model!r} content={response.content!r}")
    return True


def verify_embeddings() -> bool:
    settings = get_settings()
    provider = get_embedding_provider()
    print(f"\n[EMBEDDING] provider={settings.embedding_provider} model={provider.model_name} "
          f"device={settings.embedding_device}")

    print("[EMBEDDING] loading model + embedding a probe string (first run downloads "
          "the model — may take a while) ...", end=" ", flush=True)
    start = time.monotonic()
    healthy = provider.health_check()
    elapsed = time.monotonic() - start
    print("OK" if healthy else "FAILED", f"({elapsed:.1f}s)")
    if not healthy:
        return False

    vectors = provider.embed(["The Patels prefer a window table.", "Reservation for 4 at 7pm."])
    print(f"[EMBEDDING] dimension={provider.dimension} embedded {len(vectors)} texts, "
          f"vector[0][:5]={vectors[0][:5]}")
    return True


async def main() -> int:
    llm_ok = await verify_llm()
    embedding_ok = verify_embeddings()

    print("\n=== Summary ===")
    print(f"LLM provider (Qwen3-8B via Ollama):       {'OK' if llm_ok else 'FAILED'}")
    print(f"Embedding provider (BAAI/bge-m3):         {'OK' if embedding_ok else 'FAILED'}")

    return 0 if (llm_ok and embedding_ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
