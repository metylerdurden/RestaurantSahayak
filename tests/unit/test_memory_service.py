"""MemoryService against a mocked repository and a fake (non-BGE-M3) embedding
provider — no database, no model loading. Proves the lifecycle rules: validation,
exact-key dedup on add, access tracking on search/get, selective re-embedding on
update, reinforcement, and the soft-delete (forget) vs hard-delete (delete) split.
Real embedding generation and persistence are covered by
tests/integration/test_memory_service.py against real Postgres + real BGE-M3."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.embeddings.base import EmbeddingProvider
from app.repositories.memory_repo import MemoryRepository
from app.services.memory_service import MemoryService
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


class FakeEmbeddings(EmbeddingProvider):
    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension
        self.calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return "fake-bge-m3"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.1] * self._dimension for _ in texts]

    def health_check(self) -> bool:
        return True


def _memory(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        restaurant_id=RESTAURANT_ID,
        customer_id=None,
        agent_name=None,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a window table."},
        embedding=[0.1] * 8,
        importance=3,
        confidence=Decimal("1.00"),
        source="manager_stated",
        is_active=True,
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def repo():
    r = AsyncMock(spec=MemoryRepository)
    r.get_active_by_scope_topic.return_value = None
    r.create.side_effect = lambda **kwargs: _memory(**kwargs)
    r.save.side_effect = lambda m: m
    r.touch_access.side_effect = lambda m: m
    return r


@pytest.fixture
def embeddings():
    return FakeEmbeddings()


@pytest.fixture
def service(repo, embeddings):
    return MemoryService(repo, embeddings)


# --- add_memory ---


@pytest.mark.asyncio
async def test_add_memory_generates_embedding_and_persists(service, repo, embeddings):
    memory = await service.add_memory(
        restaurant_id=RESTAURANT_ID,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a quiet table away from the kitchen."},
        source="manager_stated",
        importance=4,
        confidence=0.9,
        customer_id=uuid.uuid4(),
    )

    assert embeddings.calls == [["seating_preference: Prefers a quiet table away from the kitchen."]]
    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["embedding"] == [0.1] * 8
    assert kwargs["importance"] == 4
    assert kwargs["confidence"] == Decimal("0.9")
    assert kwargs["is_active"] is True
    assert memory.topic == "seating_preference"


@pytest.mark.asyncio
async def test_add_memory_rejects_unknown_memory_type(service):
    with pytest.raises(ToolError) as exc_info:
        await service.add_memory(
            restaurant_id=RESTAURANT_ID,
            memory_type="NOT_A_TYPE",
            topic="x",
            content={"text": "x"},
            source="manager_stated",
        )
    assert exc_info.value.code == "invalid_memory_type"


@pytest.mark.asyncio
async def test_add_memory_rejects_unknown_source(service):
    with pytest.raises(ToolError) as exc_info:
        await service.add_memory(
            restaurant_id=RESTAURANT_ID,
            memory_type="CUSTOMER_PREFERENCE",
            topic="x",
            content={"text": "x"},
            source="not_a_source",
        )
    assert exc_info.value.code == "invalid_source"


@pytest.mark.asyncio
async def test_add_memory_rejects_out_of_range_importance(service):
    with pytest.raises(ToolError) as exc_info:
        await service.add_memory(
            restaurant_id=RESTAURANT_ID,
            memory_type="CUSTOMER_PREFERENCE",
            topic="x",
            content={"text": "x"},
            source="manager_stated",
            importance=6,
        )
    assert exc_info.value.code == "invalid_importance"


@pytest.mark.asyncio
async def test_add_memory_rejects_out_of_range_confidence(service):
    with pytest.raises(ToolError) as exc_info:
        await service.add_memory(
            restaurant_id=RESTAURANT_ID,
            memory_type="CUSTOMER_PREFERENCE",
            topic="x",
            content={"text": "x"},
            source="manager_stated",
            confidence=1.5,
        )
    assert exc_info.value.code == "invalid_confidence"


@pytest.mark.asyncio
async def test_add_memory_deactivates_conflicting_active_memory_first(service, repo):
    conflicting = _memory(topic="seating_preference", content={"text": "Prefers a window table."})
    repo.get_active_by_scope_topic.return_value = conflicting

    await service.add_memory(
        restaurant_id=RESTAURANT_ID,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a quiet table, no longer wants the window."},
        source="manager_stated",
        customer_id=uuid.uuid4(),
    )

    assert conflicting.is_active is False
    repo.save.assert_any_call(conflicting)
    repo.create.assert_called_once()  # a fresh row, not an in-place update


# --- search_memory ---


@pytest.mark.asyncio
async def test_search_memory_embeds_query_and_touches_access(service, repo, embeddings):
    found = _memory()
    repo.search_by_embedding.return_value = [(found, 0.1)]

    results = await service.search_memory(restaurant_id=RESTAURANT_ID, query="seating preference")

    assert embeddings.calls[-1] == ["seating preference"]
    assert len(results) == 1
    memory, similarity = results[0]
    assert memory is found
    assert similarity == pytest.approx(0.9)
    repo.touch_access.assert_called_once_with(found)


@pytest.mark.asyncio
async def test_search_memory_filters_results_below_min_similarity(service, repo):
    close = _memory(topic="close")
    far = _memory(topic="far")
    repo.search_by_embedding.return_value = [(close, 0.1), (far, 0.9)]

    results = await service.search_memory(restaurant_id=RESTAURANT_ID, query="x", min_similarity=0.5)

    assert [m.topic for m, _ in results] == ["close"]
    repo.touch_access.assert_called_once_with(close)


@pytest.mark.asyncio
async def test_search_memory_rejects_unknown_memory_type(service):
    with pytest.raises(ToolError) as exc_info:
        await service.search_memory(restaurant_id=RESTAURANT_ID, query="x", memory_type="NOT_A_TYPE")
    assert exc_info.value.code == "invalid_memory_type"


# --- get_memory ---


@pytest.mark.asyncio
async def test_get_memory_touches_access_by_default(service, repo):
    found = _memory()
    repo.get_by_id.return_value = found

    result = await service.get_memory(restaurant_id=RESTAURANT_ID, memory_id=found.id)

    assert result is found
    repo.touch_access.assert_called_once_with(found)


@pytest.mark.asyncio
async def test_get_memory_can_skip_touching_access(service, repo):
    found = _memory()
    repo.get_by_id.return_value = found

    await service.get_memory(restaurant_id=RESTAURANT_ID, memory_id=found.id, touch=False)

    repo.touch_access.assert_not_called()


@pytest.mark.asyncio
async def test_get_memory_not_found_raises(service, repo):
    repo.get_by_id.return_value = None
    with pytest.raises(ToolError) as exc_info:
        await service.get_memory(restaurant_id=RESTAURANT_ID, memory_id=uuid.uuid4())
    assert exc_info.value.code == "memory_not_found"


@pytest.mark.asyncio
async def test_get_memory_from_another_restaurant_raises_not_found(service, repo):
    found = _memory(restaurant_id=uuid.uuid4())  # a different restaurant
    repo.get_by_id.return_value = found
    with pytest.raises(ToolError) as exc_info:
        await service.get_memory(restaurant_id=RESTAURANT_ID, memory_id=found.id)
    assert exc_info.value.code == "memory_not_found"


# --- update_memory ---


@pytest.mark.asyncio
async def test_update_memory_regenerates_embedding_when_content_changes(service, repo, embeddings):
    existing = _memory()
    repo.get_by_id.return_value = existing

    await service.update_memory(
        restaurant_id=RESTAURANT_ID, memory_id=existing.id, content={"text": "Now prefers a booth."}
    )

    assert existing.content == {"text": "Now prefers a booth."}
    assert embeddings.calls[-1] == ["seating_preference: Now prefers a booth."]
    assert existing.embedding == [0.1] * 8


@pytest.mark.asyncio
async def test_update_memory_does_not_reembed_when_only_importance_changes(service, repo, embeddings):
    existing = _memory()
    repo.get_by_id.return_value = existing

    await service.update_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id, importance=5)

    assert existing.importance == 5
    assert embeddings.calls == []  # no re-embedding triggered


@pytest.mark.asyncio
async def test_update_memory_rejects_inactive_memory(service, repo):
    existing = _memory(is_active=False)
    repo.get_by_id.return_value = existing

    with pytest.raises(ToolError) as exc_info:
        await service.update_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id, importance=5)
    assert exc_info.value.code == "memory_inactive"


# --- reinforce_memory ---


@pytest.mark.asyncio
async def test_reinforce_memory_increases_confidence_and_touches_access(service, repo):
    existing = _memory(confidence=Decimal("0.50"))
    repo.get_by_id.return_value = existing

    result = await service.reinforce_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id, confidence_step=0.2)

    assert result.confidence == Decimal("0.7")
    repo.touch_access.assert_called_once_with(existing)


@pytest.mark.asyncio
async def test_reinforce_memory_caps_confidence_at_one(service, repo):
    existing = _memory(confidence=Decimal("0.95"))
    repo.get_by_id.return_value = existing

    result = await service.reinforce_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id, confidence_step=0.5)

    assert result.confidence == Decimal("1.0")


@pytest.mark.asyncio
async def test_reinforce_memory_rejects_inactive_memory(service, repo):
    existing = _memory(is_active=False)
    repo.get_by_id.return_value = existing
    with pytest.raises(ToolError) as exc_info:
        await service.reinforce_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id)
    assert exc_info.value.code == "memory_inactive"


# --- forget_memory / delete_memory ---


@pytest.mark.asyncio
async def test_forget_memory_soft_deletes(service, repo):
    existing = _memory(is_active=True)
    repo.get_by_id.return_value = existing

    result = await service.forget_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id, reason="outdated")

    assert result.is_active is False
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_memory_hard_deletes(service, repo):
    existing = _memory()
    repo.get_by_id.return_value = existing

    await service.delete_memory(restaurant_id=RESTAURANT_ID, memory_id=existing.id)

    repo.delete.assert_called_once_with(existing)


@pytest.mark.asyncio
async def test_delete_memory_not_found_raises(service, repo):
    repo.get_by_id.return_value = None
    with pytest.raises(ToolError) as exc_info:
        await service.delete_memory(restaurant_id=RESTAURANT_ID, memory_id=uuid.uuid4())
    assert exc_info.value.code == "memory_not_found"
