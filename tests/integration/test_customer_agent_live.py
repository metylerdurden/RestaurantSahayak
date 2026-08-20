"""CustomerAgent exercised end to end against real Postgres, a real BGE-M3 embedding
model, and the real Qwen3-8B model served by Ollama. Skips automatically (rather than
failing) if Ollama or the configured model isn't reachable.

The two scenarios requested for Phase 6:

1. Persistence across completely separate agent executions: one CustomerAgent
   execution records a preference; a second, brand-new CustomerAgent instance (no
   shared in-memory state — everything it knows comes from a fresh search_memory
   call against Postgres) retrieves it later.
2. Correction: a second statement that contradicts an earlier one must update/
   deactivate the old memory rather than silently piling up a contradictory duplicate.

As with the live reservation-agent tests, assertions favor invariants that must
always hold (a tool that resolves the customer name is used, no crashes, the
corrected fact ends up active in the database) over pinning the model's exact turn
count or phrasing.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agents.customer_agent import CustomerAgent
from app.core.config import get_settings
from app.embeddings.bge_provider import BGEEmbeddingProvider
from app.llm.factory import build_llm_provider
from app.models import Memory
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.memory_repo import MemoryRepository
from app.services.agent_run_service import AgentRunService
from app.services.customer_service import CustomerService
from app.services.memory_service import MemoryService
from app.tools.customer_tools import GetCustomerHistoryTool, GetCustomerTool, UpdateCustomerTool
from app.tools.memory_tools import (
    AddMemoryTool,
    DeleteMemoryTool,
    ForgetMemoryTool,
    GetMemoryTool,
    ReinforceMemoryTool,
    SearchMemoryTool,
    UpdateMemoryTool,
)
from tests.integration.factories import make_customer, make_restaurant, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def embedder():
    return BGEEmbeddingProvider(model_name="BAAI/bge-m3")


@pytest_asyncio.fixture
async def llm():
    provider = build_llm_provider(get_settings())
    if not await provider.health_check():
        pytest.skip("Ollama is not reachable, or the configured LLM_MODEL is not pulled")
    return provider


def _build_agent(db_session, llm, embedder, *, max_iterations: int = 8) -> CustomerAgent:
    customer_service = CustomerService(CustomerRepository(db_session))
    memory_service = MemoryService(MemoryRepository(db_session), embedder)
    agent_run_service = AgentRunService(AgentRunRepository(db_session))

    tools = [
        GetCustomerTool(customer_service),
        GetCustomerHistoryTool(customer_service),
        UpdateCustomerTool(customer_service),
        AddMemoryTool(memory_service),
        SearchMemoryTool(memory_service),
        GetMemoryTool(memory_service),
        UpdateMemoryTool(memory_service),
        ReinforceMemoryTool(memory_service),
        ForgetMemoryTool(memory_service),
        DeleteMemoryTool(memory_service),
    ]
    return CustomerAgent(llm=llm, tools=tools, agent_run_service=agent_run_service, max_iterations=max_iterations)


def _tool_names(result) -> set[str]:
    return {tc.tool_name for tc in result.tool_calls}


async def _active_customer_preference_texts(db_session, customer_id) -> list[str]:
    rows = (
        (
            await db_session.execute(
                select(Memory).where(
                    Memory.customer_id == customer_id,
                    Memory.memory_type == "CUSTOMER_PREFERENCE",
                    Memory.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return [str(m.content.get("text", "")).lower() for m in rows]


async def test_memory_persists_across_separate_agent_executions(db_session, llm, embedder):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551240001")

    # Interaction 1: a brand-new CustomerAgent instance records the preference.
    agent1 = _build_agent(db_session, llm, embedder)
    result1 = await agent1.handle(
        "Raj prefers quiet tables away from the kitchen.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )
    assert result1.status != "error", result1.summary
    assert "get_customer" in _tool_names(result1)

    # Interaction 2: a completely separate CustomerAgent instance/execution — it
    # shares no in-memory state with agent1, only what's now in Postgres.
    agent2 = _build_agent(db_session, llm, embedder)
    result2 = await agent2.handle(
        "Raj is coming Friday at 8. Anything I should know before I seat him?",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )
    assert result2.status != "error", result2.summary
    assert "search_memory" in _tool_names(result2), "the second execution must retrieve memory, not answer from nothing"

    retrieved_texts = [
        str(r.get("memory", {}).get("content", {}).get("text", "")).lower()
        for tc in result2.tool_calls
        if tc.tool_name == "search_memory"
        for r in tc.output.get("results", [])
    ]
    assert any("quiet" in t for t in retrieved_texts), retrieved_texts

    # And it's genuinely in the database, independent of what either agent reported.
    active_texts = await _active_customer_preference_texts(db_session, raj.id)
    assert any("quiet" in t for t in active_texts), active_texts


async def test_memory_correction_updates_rather_than_duplicates(db_session, llm, embedder):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551240002")

    agent1 = _build_agent(db_session, llm, embedder)
    result1 = await agent1.handle(
        "Raj prefers a table near the window.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )
    assert result1.status != "error", result1.summary

    agent2 = _build_agent(db_session, llm, embedder)
    result2 = await agent2.handle(
        "Raj no longer wants a window table. He prefers a quiet table.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )
    assert result2.status != "error", result2.summary

    active_texts = await _active_customer_preference_texts(db_session, raj.id)
    assert any("quiet" in t for t in active_texts), active_texts
    # No currently-active memory may still assert the plain, uncorrected old preference.
    assert not any(t.strip() == "prefers a table near the window." for t in active_texts), active_texts
    # The system must not have blindly piled up contradictory duplicates for this one fact.
    window_or_quiet = [t for t in active_texts if "window" in t or "quiet" in t]
    assert len(window_or_quiet) <= 1, window_or_quiet
