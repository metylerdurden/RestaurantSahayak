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


@pytest.mark.asyncio
async def test_generate_sends_tools_and_parses_tool_calls_from_response(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_customer", "arguments": {"query": "Raj"}}}
                    ],
                },
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    tool_schema = {"type": "function", "function": {"name": "get_customer", "parameters": {}}}
    response = await provider.generate([LLMMessage(role="user", content="find Raj")], tools=[tool_schema])

    assert b'"tools":[' in captured["body"]
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_customer"
    assert response.tool_calls[0].arguments == {"query": "Raj"}
    assert response.content == ""


@pytest.mark.asyncio
async def test_generate_replays_assistant_tool_calls_into_history(monkeypatch):
    from app.llm.base import ToolCall

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"model": "qwen3:8b", "message": {"content": "done"}})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))

    provider = OllamaLLMProvider(model="qwen3:8b", base_url="http://localhost:11434")
    history = [
        LLMMessage(role="user", content="find Raj"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_0", name="get_customer", arguments={"query": "Raj"})],
        ),
        LLMMessage(role="tool", content='{"customers": []}'),
    ]
    await provider.generate(history)

    assert b'"tool_calls":[{"function":{"name":"get_customer"' in captured["body"]
