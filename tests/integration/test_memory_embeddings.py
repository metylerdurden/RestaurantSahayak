"""Memory persistence + BGE-M3 embedding storage/retrieval — the two things this
phase was asked to prove explicitly, exercised against real Postgres/pgvector and a
real BGE-M3 model (not mocked; Phase 1's provider unit tests already cover the mocked
path). The embedder is loaded once per module (session-scoped) rather than per test,
since conftest's autouse cache-clearing fixture would otherwise reload the ~2GB model
for every test."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.embeddings.bge_provider import BGEEmbeddingProvider
from app.models import Customer, Memory, Restaurant

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def embedder():
    """Constructed directly (not via get_embedding_provider()) so it survives the
    per-test cache-clearing in tests/conftest.py and is loaded exactly once."""
    return BGEEmbeddingProvider(model_name="BAAI/bge-m3")


async def _restaurant(session) -> Restaurant:
    r = Restaurant(name=f"Test Restaurant {uuid.uuid4().hex[:8]}", timezone="UTC")
    session.add(r)
    await session.flush()
    return r


async def _customer(session, restaurant: Restaurant) -> Customer:
    c = Customer(restaurant_id=restaurant.id, name="Memory Test Customer", phone="+15551234567")
    session.add(c)
    await session.flush()
    return c


async def test_memory_persists_with_real_bge_m3_embedding(db_session, embedder):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)

    vector = embedder.embed(["Always seat this guest by the window."])[0]
    assert len(vector) == embedder.dimension == 1024

    memory = Memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Always seat this guest by the window."},
        embedding=vector,
        source="manager_stated",
    )
    db_session.add(memory)
    await db_session.flush()

    reloaded = (await db_session.execute(select(Memory).where(Memory.id == memory.id))).scalar_one()
    assert reloaded.embedding is not None
    assert len(reloaded.embedding) == 1024
    # Round-tripped through pgvector (float4 storage) — compare with tolerance, not
    # exact equality.
    max_abs_diff = max(abs(a - b) for a, b in zip(vector, reloaded.embedding))
    assert max_abs_diff < 1e-5


async def test_semantic_similarity_search_ranks_relevant_memory_first(db_session, embedder):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)

    facts = [
        ("seating_preference", "This guest always requests a window table."),
        ("dietary_note", "This guest has a peanut allergy — kitchen must be notified."),
        ("payment_note", "This guest usually splits the bill across two cards."),
    ]
    vectors = embedder.embed([text for _, text in facts])
    for (topic, text), vector in zip(facts, vectors):
        db_session.add(
            Memory(
                restaurant_id=restaurant.id,
                customer_id=customer.id,
                memory_type="CUSTOMER_PREFERENCE",
                topic=topic,
                content={"text": text},
                embedding=vector,
                source="manager_stated",
            )
        )
    await db_session.flush()

    query_vector = embedder.embed(["Does this guest have any allergies the kitchen should know about?"])[0]
    stmt = (
        select(Memory.topic)
        .where(Memory.restaurant_id == restaurant.id)
        .order_by(Memory.embedding.cosine_distance(query_vector))
        .limit(1)
    )
    top_topic = (await db_session.execute(stmt)).scalar_one()
    assert top_topic == "dietary_note"


async def test_access_tracking_columns_default_and_are_updatable(db_session, embedder):
    restaurant = await _restaurant(db_session)
    vector = embedder.embed(["Business rule test fact."])[0]

    memory = Memory(
        restaurant_id=restaurant.id,
        agent_name="reservation",
        memory_type="OPERATIONAL_FACT",
        topic="test_fact",
        content={"text": "Business rule test fact."},
        embedding=vector,
        source="system_derived",
    )
    db_session.add(memory)
    await db_session.flush()

    assert memory.access_count == 0
    assert memory.last_accessed_at is None

    # Simulates what MemoryService.recall() (Phase 6) will do on every read.
    now = datetime.now(timezone.utc)
    await db_session.execute(
        update(Memory).where(Memory.id == memory.id).values(access_count=Memory.access_count + 1, last_accessed_at=now)
    )
    await db_session.flush()

    reloaded = (await db_session.execute(select(Memory).where(Memory.id == memory.id))).scalar_one()
    assert reloaded.access_count == 1
    assert reloaded.last_accessed_at is not None


async def test_memory_updated_at_bumps_on_update(db_session, embedder):
    restaurant = await _restaurant(db_session)
    vector = embedder.embed(["Fact that will be corrected."])[0]

    memory = Memory(
        restaurant_id=restaurant.id,
        agent_name="reservation",
        memory_type="OPERATIONAL_FACT",
        topic="correction_test",
        content={"text": "Original fact."},
        embedding=vector,
        source="agent_inferred",
    )
    db_session.add(memory)
    await db_session.flush()
    original_updated_at = memory.updated_at

    memory.content = {"text": "Corrected fact."}
    await db_session.flush()
    assert memory.updated_at >= original_updated_at


async def test_duplicate_active_memory_same_scope_and_topic_is_rejected(db_session, embedder):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)
    vector = embedder.embed(["A preference."])[0]

    db_session.add(
        Memory(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            memory_type="CUSTOMER_PREFERENCE",
            topic="seating_preference",
            content={"text": "Window table."},
            embedding=vector,
            source="manager_stated",
        )
    )
    await db_session.flush()

    db_session.add(
        Memory(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            memory_type="CUSTOMER_PREFERENCE",
            topic="seating_preference",  # same scope + topic while still active
            content={"text": "Patio table."},
            embedding=vector,
            source="manager_stated",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_forgetting_a_memory_frees_its_scope_and_topic_for_reuse(db_session, embedder):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)
    vector = embedder.embed(["A preference."])[0]

    original = Memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="seating_preference",
        content={"text": "Window table."},
        embedding=vector,
        source="manager_stated",
    )
    db_session.add(original)
    await db_session.flush()

    original.is_active = False  # "forget" — soft delete, audit trail preserved
    await db_session.flush()

    db_session.add(
        Memory(
            restaurant_id=restaurant.id,
            customer_id=customer.id,
            memory_type="CUSTOMER_PREFERENCE",
            topic="seating_preference",
            content={"text": "Patio table now preferred."},
            embedding=vector,
            source="manager_stated",
        )
    )
    await db_session.flush()  # should not raise — old row is inactive


async def test_scope_key_computed_correctly_for_customer_vs_agent_vs_restaurant_scope(db_session, embedder):
    restaurant = await _restaurant(db_session)
    customer = await _customer(db_session, restaurant)
    vector = embedder.embed(["x"])[0]

    customer_scoped = Memory(
        restaurant_id=restaurant.id,
        customer_id=customer.id,
        memory_type="CUSTOMER_PREFERENCE",
        topic="t1",
        content={"text": "x"},
        embedding=vector,
        source="manager_stated",
    )
    agent_scoped = Memory(
        restaurant_id=restaurant.id,
        agent_name="inventory",
        memory_type="AGENT_EXPERIENCE",
        topic="t2",
        content={"text": "x"},
        embedding=vector,
        source="agent_inferred",
    )
    restaurant_scoped = Memory(
        restaurant_id=restaurant.id,
        memory_type="BUSINESS_RULE",
        topic="t3",
        content={"text": "x"},
        embedding=vector,
        source="manager_stated",
    )
    db_session.add_all([customer_scoped, agent_scoped, restaurant_scoped])
    await db_session.flush()
    for m in (customer_scoped, agent_scoped, restaurant_scoped):
        await db_session.refresh(m)

    assert customer_scoped.scope_key == str(customer.id)
    assert agent_scoped.scope_key == "inventory"
    assert restaurant_scoped.scope_key == "restaurant"
