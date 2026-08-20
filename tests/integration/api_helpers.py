"""Shared plumbing for Manager API (Step 19) integration tests against the real
FastAPI app + real Postgres, with the LLM/embedding providers swapped for fast,
deterministic fakes — the same pattern tests/integration/test_workflows_api.py
established. Most of these routes never touch the LLM at all (plain CRUD/read), so
an empty scripted-response queue is the common case; tests that do exercise an
agent (agent-runs trigger, workflow triggers) pass their own queue."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

import app.api.stack as stack_module
from app.api.deps import get_db_session
from app.llm.base import LLMProvider, LLMResponse, ToolCall
from app.main import create_app


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses = list(responses or [])

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, messages, *, temperature: float = 0.0, tools=None, **kwargs) -> LLMResponse:
        if not self._responses:
            return LLMResponse(content="Done.", model="fake-model")
        return self._responses.pop(0)

    async def health_check(self) -> bool:
        return True


class FakeEmbeddingProvider:
    """Dimension must match the real `memories.embedding` pgvector column (1024,
    sized for BGE-M3) — unlike test_workflows_api.py's fixture (which never writes
    a Memory row), these tests exercise ApprovalService.approve()/reject(), which
    always attempts to record a PAST_DECISION memory. A mismatched fake dimension
    fails that insert at the database level."""

    @property
    def model_name(self) -> str:
        return "fake-embeddings"

    @property
    def dimension(self) -> int:
        return 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]

    def health_check(self) -> bool:
        return True


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content="", model="fake-model", tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)]
    )


def final(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="fake-model")


def build_test_app(db_session, monkeypatch, *, responses: list[LLMResponse] | None = None) -> FastAPI:
    scripted_llm = ScriptedLLM(responses)
    monkeypatch.setattr(stack_module, "get_llm_provider", lambda: scripted_llm)
    monkeypatch.setattr(stack_module, "get_embedding_provider", lambda: FakeEmbeddingProvider())

    app = create_app()

    async def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app
