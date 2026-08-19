"""Memory tools end-to-end against real Postgres and a real BGE-M3 model — the typed
surface an agent actually calls, exercised the same way Phase 3's tool tests
exercised the reservation/inventory/customer/staffing/analytics tools."""

from __future__ import annotations

import pytest

from app.embeddings.bge_provider import BGEEmbeddingProvider
from app.repositories.memory_repo import MemoryRepository
from app.services.memory_service import MemoryService
from app.tools.base import ToolContext, ToolError
from app.tools.memory_tools import (
    AddMemoryTool,
    DeleteMemoryTool,
    ForgetMemoryTool,
    GetMemoryTool,
    ReinforceMemoryTool,
    SearchMemoryTool,
    UpdateMemoryTool,
)
from tests.integration.factories import make_agent_run, make_customer, make_restaurant, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def embedder():
    return BGEEmbeddingProvider(model_name="BAAI/bge-m3")


def _service(db_session, embedder) -> MemoryService:
    return MemoryService(MemoryRepository(db_session), embedder)


async def test_add_memory_tool_creates_a_memory(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    run = await make_agent_run(db_session, restaurant, user, agent_name="customer")
    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer", agent_run_id=run.id
    )

    output = await AddMemoryTool(service)(
        {
            "memory_type": "CUSTOMER_PREFERENCE",
            "topic": "seating_preference",
            "content": {"text": "Prefers a quiet table away from the kitchen."},
            "source": "manager_stated",
            "importance": 4,
            "confidence": 1.0,
            "customer_id": str(customer.id),
        },
        context=context,
    )

    assert output.memory.topic == "seating_preference"
    assert output.memory.customer_id == customer.id
    assert output.memory.importance == 4


async def test_search_memory_tool_finds_relevant_memory(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    await AddMemoryTool(service)(
        {
            "memory_type": "CUSTOMER_PREFERENCE",
            "topic": "dietary_note",
            "content": {"text": "Has a shellfish allergy."},
            "source": "manager_stated",
            "customer_id": str(customer.id),
        },
        context=context,
    )

    output = await SearchMemoryTool(service)(
        {"query": "Any allergies for this guest?", "customer_id": str(customer.id)}, context=context
    )

    assert len(output.results) == 1
    assert output.results[0].memory.topic == "dietary_note"
    assert output.results[0].similarity > 0.3


async def test_get_update_reinforce_forget_delete_round_trip(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    created = await AddMemoryTool(service)(
        {
            "memory_type": "BUSINESS_RULE",
            "topic": "cancellation_policy",
            "content": {"text": "24h notice required for parties of 6+."},
            "source": "manager_stated",
            "confidence": 0.7,
        },
        context=context,
    )
    memory_id = created.memory.id

    fetched = await GetMemoryTool(service)({"memory_id": str(memory_id)}, context=context)
    assert fetched.memory.id == memory_id
    assert fetched.memory.access_count == 1

    updated = await UpdateMemoryTool(service)(
        {"memory_id": str(memory_id), "importance": 5}, context=context
    )
    assert updated.memory.importance == 5

    reinforced = await ReinforceMemoryTool(service)(
        {"memory_id": str(memory_id), "confidence_step": 0.2}, context=context
    )
    assert float(reinforced.memory.confidence) == pytest.approx(0.9)

    forgotten = await ForgetMemoryTool(service)(
        {"memory_id": str(memory_id), "reason": "policy changed"}, context=context
    )
    assert forgotten.memory.is_active is False

    deleted = await DeleteMemoryTool(service)({"memory_id": str(memory_id)}, context=context)
    assert deleted.deleted is True

    with pytest.raises(ToolError) as exc_info:
        await GetMemoryTool(service)({"memory_id": str(memory_id)}, context=context)
    assert exc_info.value.code == "memory_not_found"


async def test_add_memory_tool_rejects_unknown_memory_type(db_session, embedder):
    service = _service(db_session, embedder)
    restaurant = await make_restaurant(db_session)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    with pytest.raises(Exception):  # pydantic ValidationError — not a valid Literal member
        await AddMemoryTool(service)(
            {
                "memory_type": "NOT_A_REAL_TYPE",
                "topic": "x",
                "content": {"text": "x"},
                "source": "manager_stated",
            },
            context=context,
        )
