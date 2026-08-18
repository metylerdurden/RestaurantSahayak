"""Unit tests for OllamaLLMProvider using a mocked HTTP transport — no network,
no dependency on a real Ollama server being up. Real connectivity is proven
separately by scripts/verify_providers.py and the manual verification step."""

from __future__ import annotations

import httpx
import pytest

from app.llm.base import LLMMessage
from app.llm.ollama_provider import OllamaLLMProvider


_RealAsyncClient = httpx.AsyncClient


def _client_factory(handler):
    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return _RealAsyncClient(transport=httpx.MockTransport(handler), *args, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_generate_calls_chat_endpoint_with_configured_model(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json={"model": "qwen3:8b", "message": {"content": "hello"}})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    response = await provider.generate([LLMMessage(role="user", content="hi")])

    assert response.content == "hello"
    assert response.model == "qwen3:8b"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert b'"model":"qwen3:8b"' in captured["body"]


@pytest.mark.asyncio
async def test_health_check_true_when_model_present(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, {"name": "gemma3:12b"}]})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_false_when_model_missing(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "gemma3:12b"}]})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_on_connection_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    assert await provider.health_check() is False


def test_model_name_is_never_hardcoded_in_provider():
    provider = OllamaLLMProvider(model="some-other-model:latest", base_url="http://localhost:11434")
    assert provider.model_name == "some-other-model:latest"
