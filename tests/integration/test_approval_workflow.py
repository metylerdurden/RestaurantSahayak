"""The human approval workflow end to end against real Postgres — the seven
requested scenarios: automatic low-risk actions, medium- and high-risk approvals,
rejection, expiry, and successful/failed execution after approval. Real
ReservationService/InventoryService/ApprovalService, real MemoryService + BGE-M3 for
the "store relevant memory" step. No live LLM needed here — the approval workflow
itself is deterministic infrastructure, not model reasoning; Qwen3-8B's role (an
agent deciding to propose an action) is already covered by the live agent tests in
tests/integration/test_reservation_agent_live.py etc."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.embeddings.bge_provider import BGEEmbeddingProvider
from app.models import Approval, Event, Memory
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.event_repo import EventRepository
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.memory_repo import MemoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.services.approval_execution import build_executors
from app.services.approval_service import ApprovalService
from app.services.event_bus import InProcessEventBus
from app.services.inventory_service import InventoryService
from app.services.memory_service import MemoryService
from app.services.reservation_service import ReservationService
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError, utcnow
from app.tools.inventory_tools import CreatePurchaseRequestTool, GetInventoryTool
from app.tools.reservation_tools import (
    CancelReservationTool,
    CreateReservationTool,
    GetReservationsTool,
    ModifyReservationTool,
)
from tests.integration.factories import (
    future,
    make_agent_run,
    make_customer,
    make_inventory_item,
    make_restaurant,
    make_table,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def embedder():
    return BGEEmbeddingProvider(model_name="BAAI/bge-m3")


def _plain_approval_service(db_session) -> ApprovalService:
    """No executors/memory wired — the original propose/decide-only behavior."""
    return ApprovalService(ApprovalRepository(db_session))


def _full_approval_service(
    db_session, embedder, *, reservation_service=None, inventory_service=None
) -> ApprovalService:
    executors = build_executors(reservation_service=reservation_service, inventory_service=inventory_service)
    memory_service = MemoryService(MemoryRepository(db_session), embedder)
    return ApprovalService(ApprovalRepository(db_session), executors=executors, memory_service=memory_service)


async def _make_reservation_service(db_session, approval_service) -> ReservationService:
    from app.repositories.customer_repo import CustomerRepository

    return ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )


async def _make_inventory_service(db_session, approval_service) -> InventoryService:
    return InventoryService(InventoryRepository(db_session), approval_service, get_settings())


# --- 1. automatic low-risk action ---


async def test_low_risk_action_executes_automatically_with_no_approval_created(db_session):
    """Reading inventory is LOW risk: it must never create an Approval row."""
    approval_service = _plain_approval_service(db_session)
    inventory_service = await _make_inventory_service(db_session, approval_service)
    restaurant = await make_restaurant(db_session)
    await make_inventory_item(db_session, restaurant, name="Flour")

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory")
    output = await GetInventoryTool(inventory_service)({}, context=context)

    assert output.items[0].name == "Flour"
    pending = await approval_service.get_pending_approvals(restaurant.id)
    assert pending == []


# --- 2. medium-risk approval ---


async def test_modifying_a_large_party_creates_a_medium_risk_approval(db_session):
    approval_service = _plain_approval_service(db_session)
    reservation_service = await _make_reservation_service(db_session, approval_service)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="reservation")
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()}, context=context
    )

    result = await ModifyReservationTool(reservation_service)(
        {"reservation_id": str(created.reservation.id), "party_size": 9}, context=context
    )
    assert isinstance(result, PendingApprovalOutput)

    approval = await db_session.get(Approval, result.approval_id)
    assert approval.risk_level == "MEDIUM"
    assert approval.domain == "reservation"
    assert approval.status == "pending"
    assert approval.agent_name == "reservation"


# --- 3. high-risk approval ---


async def test_purchase_above_threshold_creates_a_high_risk_approval(db_session):
    approval_service = _plain_approval_service(db_session)
    inventory_service = await _make_inventory_service(db_session, approval_service)
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user, agent_name="inventory")
    item = await make_inventory_item(db_session, restaurant, name="Olive Oil")

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="inventory", agent_run_id=agent_run.id
    )
    result = await CreatePurchaseRequestTool(inventory_service)(
        {"item_id": str(item.id), "requested_quantity": "100", "estimated_cost": "500"}, context=context
    )
    assert isinstance(result, PendingApprovalOutput)

    approval = await db_session.get(Approval, result.approval_id)
    assert approval.risk_level == "HIGH"
    assert approval.domain == "purchase"


# --- 4. rejection ---


async def test_rejection_leaves_the_action_unapplied_and_is_terminal(db_session):
    approval_service = _plain_approval_service(db_session)
    reservation_service = await _make_reservation_service(db_session, approval_service)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()}, context=context
    )
    cancel_result = await CancelReservationTool(reservation_service)(
        {"reservation_id": str(created.reservation.id)}, context=context
    )

    approval = await approval_service.reject(cancel_result.approval_id, user.id, reason="Keep the booking")
    assert approval.status == "rejected"
    assert approval.rejected_at is not None
    assert approval.execution_result is None

    with pytest.raises(ToolError) as exc_info:
        await approval_service.approve(cancel_result.approval_id, user.id)
    assert exc_info.value.code == "approval_already_decided"

    listing = await GetReservationsTool(reservation_service)({}, context=context)
    assert listing.reservations[0].status == "booked"


# --- 5. expired approval ---


async def test_expired_approval_cannot_be_approved_or_rejected(db_session):
    approval_service = _plain_approval_service(db_session)
    reservation_service = await _make_reservation_service(db_session, approval_service)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()}, context=context
    )
    cancel_result = await CancelReservationTool(reservation_service)(
        {"reservation_id": str(created.reservation.id)}, context=context
    )

    approval = await db_session.get(Approval, cancel_result.approval_id)
    approval.expires_at = utcnow() - timedelta(hours=1)
    await db_session.flush()

    expired = await approval_service.expire(restaurant.id)
    assert len(expired) == 1
    assert expired[0].id == approval.id
    assert expired[0].status == "expired"

    with pytest.raises(ToolError) as exc_info:
        await approval_service.approve(cancel_result.approval_id, user.id)
    assert exc_info.value.code == "approval_expired"

    with pytest.raises(ToolError) as exc_info:
        await approval_service.reject(cancel_result.approval_id, user.id)
    assert exc_info.value.code == "approval_expired"


# --- 6. successful execution after approval ---


async def test_approving_a_cancellation_executes_it_and_stores_a_memory(db_session, embedder):
    approval_service = _plain_approval_service(db_session)
    reservation_service = await _make_reservation_service(db_session, approval_service)
    # Full service (executors + memory) wraps the SAME repo/session, so its
    # execution is visible through reservation_service's own queries too.
    full_approval_service = _full_approval_service(db_session, embedder, reservation_service=reservation_service)
    reservation_service.approval_service = full_approval_service

    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()}, context=context
    )
    cancel_result = await CancelReservationTool(reservation_service)(
        {"reservation_id": str(created.reservation.id)}, context=context
    )

    approval = await full_approval_service.approve(cancel_result.approval_id, user.id)

    assert approval.status == "approved"
    assert approval.execution_result["status"] == "success"
    assert approval.execution_result["result"]["status"] == "cancelled"
    assert approval.executed_at is not None

    listing = await GetReservationsTool(reservation_service)({"status": "cancelled"}, context=context)
    assert listing.reservations[0].id == created.reservation.id

    memories = (await db_session.execute(select(Memory).where(Memory.restaurant_id == restaurant.id))).scalars().all()
    assert len(memories) == 1
    assert memories[0].memory_type == "PAST_DECISION"
    assert memories[0].topic == f"approval_{approval.id}"
    assert "cancel" in memories[0].content["text"].lower()


# --- 7. failed execution after approval ---


async def test_approving_an_action_whose_execution_then_fails_records_the_failure(db_session, embedder):
    approval_service = _plain_approval_service(db_session)
    reservation_service = await _make_reservation_service(db_session, approval_service)
    full_approval_service = _full_approval_service(db_session, embedder, reservation_service=reservation_service)
    reservation_service.approval_service = full_approval_service

    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()}, context=context
    )
    cancel_result = await CancelReservationTool(reservation_service)(
        {"reservation_id": str(created.reservation.id)}, context=context
    )

    # Simulate the reservation having been deleted/become unreachable between the
    # proposal and the decision, so execute_approved_action fails at decision time.
    reservation = await reservation_service.repo.get_by_id(created.reservation.id)
    await db_session.delete(reservation)
    await db_session.flush()

    approval = await full_approval_service.approve(cancel_result.approval_id, user.id)

    # The decision itself stands — a manager did approve it — only execution failed.
    assert approval.status == "approved"
    assert approval.execution_result["status"] == "failed"
    assert (
        "reservation_not_found" in approval.execution_result["error"]
        or "No reservation found" in approval.execution_result["error"]
    )
    assert approval.executed_at is not None

    memories = (await db_session.execute(select(Memory).where(Memory.restaurant_id == restaurant.id))).scalars().all()
    assert len(memories) == 1
    assert "failed" in memories[0].content["text"].lower()


# --- Step 20 reliability hardening: a database-level failure recording the
# approval memory must not poison the session for what follows ---


class _WrongDimensionEmbedder:
    """Deliberately returns vectors of the wrong size, so MemoryService.add_memory
    fails at the database level (a real pgvector dimension mismatch), not just
    with a caught Python-level exception — the two behave very differently inside
    a SQLAlchemy transaction (see ApprovalService._execute/_remember)."""

    @property
    def model_name(self) -> str:
        return "broken-embedder"

    @property
    def dimension(self) -> int:
        return 8

    def embed(self, texts):
        return [[0.1] * 8 for _ in texts]

    def health_check(self) -> bool:
        return True


async def test_a_database_level_memory_recording_failure_does_not_poison_the_rest_of_the_decision(db_session):
    approval_service = _plain_approval_service(db_session)
    reservation_service = await _make_reservation_service(db_session, approval_service)

    broken_memory_service = MemoryService(MemoryRepository(db_session), _WrongDimensionEmbedder())
    event_bus = InProcessEventBus(EventRepository(db_session))
    full_approval_service = ApprovalService(
        ApprovalRepository(db_session),
        executors=build_executors(reservation_service=reservation_service),
        memory_service=broken_memory_service,
        event_bus=event_bus,
    )
    reservation_service.approval_service = full_approval_service

    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation", agent_run_id=agent_run.id
    )
    created = await CreateReservationTool(reservation_service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()}, context=context
    )
    cancel_result = await CancelReservationTool(reservation_service)(
        {"reservation_id": str(created.reservation.id)}, context=context
    )

    # Must not raise: memory recording fails for real (wrong pgvector dimension),
    # but the approval decision and the event-publish call right after it must
    # both still go through — that's the whole point of the SAVEPOINT isolation.
    approval = await full_approval_service.approve(cancel_result.approval_id, user.id)

    assert approval.status == "approved"
    assert approval.execution_result["status"] == "success"

    # No memory was actually persisted — the failure was real, not silently
    # swallowed into a fake success.
    memories = (await db_session.execute(select(Memory).where(Memory.restaurant_id == restaurant.id))).scalars().all()
    assert memories == []

    # ...but the session stayed usable: the approval.completed event published
    # immediately afterward, in the same request, actually made it to the database.
    events = (
        (
            await db_session.execute(
                select(Event).where(Event.restaurant_id == restaurant.id, Event.event_type == "approval.completed")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
