"""MemoryService against real Postgres/pgvector and a real BGE-M3 model (not
mocked). The embedder is module-scoped so the ~2GB model loads once for the whole
file rather than once per test (conftest's autouse cache-clearing fixture would
otherwise force a reload every test)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.embeddings.bge_provider import BGEEmbeddingProvider
from app.repositories.memory_repo import MemoryRepository
from app.services.memory_service import MemoryService
from app.tools.base import ToolError
from tests.integration.factories import make_customer, make_restaurant

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def embedder():
    return BGEEmbeddingProvider(model_name="BAAI/bge-m3")


def _service(db_session, embedder) -> MemoryService:
    return MemoryService(MemoryRepository(db_session), embedder)


async def test_add_memory_creates_with_real_bge_m3_embedding(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)

    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Always seat this guest by the window."},
        source="manager_stated",
        importance=4,
        confidence=0.95,
    )

    assert memory.id is not None
    assert memory.embedding is not None
    assert len(memory.embedding) == embedder.dimension == 1024
    assert memory.importance == 4
    assert memory.confidence == Decimal("0.95")
    assert memory.is_active is True
    assert memory.access_count == 0


async def test_add_memory_correction_deactivates_old_memory_and_creates_new_one(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)

    original = await service.add_memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Prefers a table near the window."},
        source="manager_stated",
    )

    corrected = await service.add_memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "No longer wants a window table. Prefers a quiet table."},
        source="manager_stated",
    )

    assert corrected.id != original.id
    reloaded_original = await service.get_memory(
        restaurant_id=restaurant.id, memory_id=original.id, touch=False
    )
    assert reloaded_original.is_active is False
    assert corrected.is_active is True

    results = await service.search_memory(
        restaurant_id=restaurant.id, query="seating preference", customer_id=customer.id
    )
    active_topics = [m.content["text"] for m, _ in results]
    assert "No longer wants a window table. Prefers a quiet table." in active_topics
    assert "Prefers a table near the window." not in active_topics  # inactive, excluded from search


async def test_semantic_search_ranks_relevant_memory_first(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)

    facts = [
        ("seating_preference", "This guest always requests a window table."),
        ("dietary_note", "This guest has a peanut allergy — kitchen must be notified."),
        ("payment_note", "This guest usually splits the bill across two cards."),
    ]
    for topic, text in facts:
        await service.add_memory(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            memory_type="CUSTOMER_PREFERENCE",
            topic=topic,
            content={"text": text},
            source="manager_stated",
        )

    results = await service.search_memory(
        restaurant_id=restaurant.id,
        query="Does this guest have any allergies the kitchen should know about?",
        top_k=1,
    )

    assert len(results) == 1
    assert results[0][0].topic == "dietary_note"


async def test_search_memory_touches_access_count_and_last_accessed(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)

    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Window table, always."},
        source="manager_stated",
    )
    assert memory.access_count == 0
    assert memory.last_accessed_at is None

    await service.search_memory(restaurant_id=restaurant.id, query="seating", customer_id=customer.id)
    await service.search_memory(restaurant_id=restaurant.id, query="seating", customer_id=customer.id)

    reloaded = await service.get_memory(restaurant_id=restaurant.id, memory_id=memory.id, touch=False)
    assert reloaded.access_count == 2
    assert reloaded.last_accessed_at is not None


async def test_get_memory_touches_access(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        agent_name="reservation",
        memory_type="OPERATIONAL_FACT",
        topic="peak_hours",
        content={"text": "Friday and Saturday 7-9pm are the busiest."},
        source="system_derived",
    )

    fetched = await service.get_memory(restaurant_id=restaurant.id, memory_id=memory.id)
    assert fetched.access_count == 1

    fetched_again = await service.get_memory(restaurant_id=restaurant.id, memory_id=memory.id, touch=False)
    assert fetched_again.access_count == 1  # unchanged — touch=False


async def test_update_memory_changes_content_and_regenerates_embedding(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="dietary_note",
        content={"text": "No known allergies."},
        source="manager_stated",
        confidence=0.6,
    )
    original_embedding = list(memory.embedding)

    updated = await service.update_memory(
        restaurant_id=restaurant.id,
        memory_id=memory.id,
        content={"text": "Has a peanut allergy — confirmed by the guest directly."},
        confidence=1.0,
    )

    assert updated.content["text"] == "Has a peanut allergy — confirmed by the guest directly."
    assert updated.confidence == Decimal("1.0")
    assert list(updated.embedding) != original_embedding
    assert updated.updated_at >= updated.created_at


async def test_reinforce_memory_increases_confidence_and_tracks_access(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        memory_type="BUSINESS_RULE",
        topic="cancellation_policy",
        content={"text": "Parties of 6+ require 24h notice to cancel."},
        source="manager_stated",
        confidence=0.5,
    )

    reinforced = await service.reinforce_memory(
        restaurant_id=restaurant.id, memory_id=memory.id, confidence_step=0.2
    )

    assert reinforced.confidence == Decimal("0.7")
    assert reinforced.access_count == 1


async def test_forget_memory_deactivates_and_excludes_from_search(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="occasion",
        content={"text": "Usually celebrating a birthday."},
        source="agent_inferred",
        confidence=0.6,
    )

    forgotten = await service.forget_memory(restaurant_id=restaurant.id, memory_id=memory.id, reason="stale")
    assert forgotten.is_active is False

    results = await service.search_memory(
        restaurant_id=restaurant.id, query="birthday celebration", customer_id=customer.id
    )
    assert memory.id not in [m.id for m, _ in results]


async def test_delete_memory_hard_deletes(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    memory = await service.add_memory(
        restaurant_id=restaurant.id,
        memory_type="AGENT_EXPERIENCE",
        agent_name="reservation",
        topic="scratch_note",
        content={"text": "Recorded in error."},
        source="agent_inferred",
    )

    await service.delete_memory(restaurant_id=restaurant.id, memory_id=memory.id)

    with pytest.raises(ToolError) as exc_info:
        await service.get_memory(restaurant_id=restaurant.id, memory_id=memory.id)
    assert exc_info.value.code == "memory_not_found"


async def test_memory_isolated_per_restaurant(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant_a = await make_restaurant(db_session)
    restaurant_b = await make_restaurant(db_session)
    memory = await service.add_memory(
        restaurant_id=restaurant_a.id,
        memory_type="OPERATIONAL_FACT",
        topic="cross_tenant_test",
        content={"text": "Belongs to restaurant A only."},
        source="system_derived",
    )

    with pytest.raises(ToolError) as exc_info:
        await service.get_memory(restaurant_id=restaurant_b.id, memory_id=memory.id)
    assert exc_info.value.code == "memory_not_found"
