"""LLMProvider implementation backed by a local Ollama server.

Model name is never hard-coded here — it is passed in at construction time (see
app.llm.factory), sourced from Settings.llm_model (env var LLM_MODEL, default
"qwen3:8b" for local development per DineOps' agent LLM choice).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.llm.base import LLMMessage, LLMProvider, LLMResponse


class OllamaLLMProvider(LLMProvider):
    def __init__(self, model: str, base_url: str, timeout_seconds: float = 120.0) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        payload.update(kwargs)

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data.get("message", {}).get("content", ""),
            model=data.get("model", self._model),
            raw=data,
        )

    async def health_check(self) -> bool:
        """Fast check: Ollama is reachable and the configured model is pulled.

        Deliberately does not run a generation (that's slow) — this is what backs
        GET /health/ready, which should stay cheap.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            return False

        available = {m.get("name", "") for m in data.get("models", [])}
        # Ollama model names may or may not include a ":tag" suffix; match on the
        # base name so "qwen3:8b" matches whether or not a tag was configured.
        base_name = self._model.split(":", 1)[0]
        return any(name == self._model or name.split(":", 1)[0] == base_name for name in available)
