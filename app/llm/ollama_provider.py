"""LLMProvider implementation backed by a local Ollama server.

Model name is never hard-coded here — it is passed in at construction time (see
app.llm.factory), sourced from Settings.llm_model (env var LLM_MODEL, default
"qwen3:8b" for local development per DineOps' agent LLM choice).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.core.telemetry import get_tracer, start_span
from app.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolCall

_tracer = get_tracer(__name__)

# Retried: pure connectivity/timeout failures — Ollama not yet up, a momentary
# network blip, a slow model load. NOT retried: httpx.HTTPStatusError (Ollama
# responded but rejected the request — e.g. an unknown model name — retrying
# the identical request would just fail identically) or anything else, so a
# genuinely broken request fails fast instead of masking the real problem
# behind a few seconds of pointless retrying.
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)


def _message_to_payload(message: LLMMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in message.tool_calls
        ]
    return payload


def _parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    raw_calls = message.get("tool_calls") or []
    return [
        ToolCall(
            id=f"call_{i}",
            name=call.get("function", {}).get("name", ""),
            arguments=call.get("function", {}).get("arguments", {}) or {},
        )
        for i, call in enumerate(raw_calls)
    ]


class OllamaLLMProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        base_url: str,
        timeout_seconds: float = 120.0,
        *,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        # Bounded — a fixed number of attempts, not an infinite retry loop. Ollama
        # being unavailable after these attempts is reported as a clear failure
        # (see generate()), never silently swallowed or retried forever.
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_message_to_payload(m) for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        with start_span(
            _tracer, "llm.call", model_name=self._model, model_provider="ollama", has_tools=bool(tools)
        ) as span:
            started_at = time.monotonic()
            attempt = 0
            while True:
                try:
                    async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                        response = await client.post(f"{self._base_url}/api/chat", json=payload)
                        response.raise_for_status()
                        data = response.json()
                    break
                except _RETRYABLE_EXCEPTIONS as exc:
                    attempt += 1
                    if attempt > self._max_retries:
                        span.set_attribute("success", False)
                        span.set_attribute("attempts", attempt)
                        raise
                    span.add_event("llm.call.retry", {"attempt": attempt, "error_type": type(exc).__name__})
                    await asyncio.sleep(self._retry_backoff_seconds)
                except Exception:
                    span.set_attribute("success", False)
                    raise

            span.set_attribute("success", True)
            span.set_attribute("attempts", attempt + 1)
            span.set_attribute("latency_ms", int((time.monotonic() - started_at) * 1000))
            # Ollama reports token counts when available (not every provider does).
            if data.get("prompt_eval_count") is not None:
                span.set_attribute("llm.prompt_tokens", data["prompt_eval_count"])
            if data.get("eval_count") is not None:
                span.set_attribute("llm.completion_tokens", data["eval_count"])

            message = data.get("message", {})
            tool_calls = _parse_tool_calls(message)
            span.set_attribute("tool_call_count", len(tool_calls))
            return LLMResponse(
                content=message.get("content", "") or "",
                model=data.get("model", self._model),
                tool_calls=tool_calls,
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
