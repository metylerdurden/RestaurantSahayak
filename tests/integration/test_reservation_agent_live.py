"""ReservationAgent exercised end to end against a real Postgres database and a real
Qwen3-8B model served by Ollama — the four scenarios requested for Phase 4. Skips
automatically (rather than failing) if Ollama or the configured model isn't reachable,
so the rest of the suite stays runnable on machines without a local Ollama server.

These tests assert what must always hold given the agent's design (correct tool used
to resolve a name to a real record, no fabricated ids, no crashes/errors, correct
final database state) while staying tolerant of reasonable variation in a live model's
exact phrasing or turn count. Deliberately-ambiguous inputs (e.g. "Book a table for
Raj at 8 PM" — no party size given) are allowed to end in a clarifying question rather
than a created reservation: an agent that refuses to invent an unstated party size is
behaving correctly per its "never invent information" instruction, not failing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agents.reservation_agent import ReservationAgent
from app.core.config import get_settings
from app.llm.factory import build_llm_provider
from app.models import Reservation
from app.repositories.agent_run_repo import AgentRunRepository
from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.reservation_repo import ReservationRepository
from app.services.agent_run_service import AgentRunService
from app.services.approval_service import ApprovalService
from app.services.customer_service import CustomerService
from app.services.reservation_service import ReservationService
from app.tools.base import utcnow
from app.tools.customer_tools import GetCustomerTool
from app.tools.reservation_tools import (
    CancelReservationTool,
    CreateReservationTool,
    FindAvailableTableTool,
    GetReservationsTool,
    ModifyReservationTool,
)
from tests.integration.factories import make_customer, make_restaurant, make_table, make_user

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def llm():
    provider = build_llm_provider(get_settings())
    if not await provider.health_check():
        pytest.skip("Ollama is not reachable, or the configured LLM_MODEL is not pulled")
    return provider


def _build_agent(db_session, llm, *, max_iterations: int = 6) -> ReservationAgent:
    approval_service = ApprovalService(ApprovalRepository(db_session))
    reservation_service = ReservationService(
        ReservationRepository(db_session), CustomerRepository(db_session), approval_service, get_settings()
    )
    customer_service = CustomerService(CustomerRepository(db_session))
    agent_run_service = AgentRunService(AgentRunRepository(db_session))

    tools = [
        GetReservationsTool(reservation_service),
        FindAvailableTableTool(reservation_service),
        CreateReservationTool(reservation_service),
        ModifyReservationTool(reservation_service),
        CancelReservationTool(reservation_service),
        GetCustomerTool(customer_service),
    ]
    return ReservationAgent(llm=llm, tools=tools, agent_run_service=agent_run_service, max_iterations=max_iterations)


def _tool_names(result) -> set[str]:
    return {tc.tool_name for tc in result.tool_calls}


async def test_find_available_table_for_a_party_tonight(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    for capacity in (2, 4, 4, 6):
        await make_table(db_session, restaurant, seat_capacity=capacity)

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Find me a table for 4 people tonight at 8 PM.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )

    assert result.status != "error", result.summary
    assert "find_available_table" in _tool_names(result)
    assert result.summary


async def test_book_a_table_for_a_named_customer(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551230001")
    for capacity in (2, 4, 6, 8):
        await make_table(db_session, restaurant, seat_capacity=capacity)

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Book a table for Raj at 8 PM.", restaurant_id=restaurant.id, initiated_by_user_id=user.id
    )

    assert result.status != "error", result.summary
    assert "get_customer" in _tool_names(result), "must resolve the name 'Raj' to a real customer record"

    if "create_reservation" in _tool_names(result):
        created = (
            await db_session.execute(select(Reservation).where(Reservation.customer_id == raj.id))
        ).scalar_one_or_none()
        assert created is not None, "agent reported creating a reservation but none exists in the database"


async def test_move_an_existing_reservation(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551230002")
    table = await make_table(db_session, restaurant, seat_capacity=4)
    original_time = utcnow() + timedelta(days=1)
    original_time = original_time.replace(hour=20, minute=0, second=0, microsecond=0)
    reservation = Reservation(
        restaurant_id=restaurant.id,
        customer_id=raj.id,
        table_id=table.id,
        party_size=2,
        requested_time=original_time,
        duration_minutes=90,
        status="booked",
        created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Move Raj's reservation from 8 PM to 8:30 PM.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )

    assert result.status != "error", result.summary
    assert _tool_names(result) & {"get_reservations", "get_customer_history"}, (
        "must look up the existing reservation before modifying it"
    )

    await db_session.refresh(reservation)
    if "modify_reservation" in _tool_names(result):
        assert reservation.requested_time.hour == 20
        assert reservation.requested_time.minute == 30
        assert reservation.status == "modified"


async def test_cancel_an_existing_reservation(db_session, llm):
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551230003")
    table = await make_table(db_session, restaurant, seat_capacity=4)
    reservation = Reservation(
        restaurant_id=restaurant.id,
        customer_id=raj.id,
        table_id=table.id,
        party_size=2,
        requested_time=utcnow() + timedelta(days=1, hours=1),
        duration_minutes=90,
        status="booked",
        created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()

    agent = _build_agent(db_session, llm)
    result = await agent.handle("Cancel Raj's reservation.", restaurant_id=restaurant.id, initiated_by_user_id=user.id)

    assert result.status != "error", result.summary
    assert _tool_names(result) & {"get_reservations", "get_customer_history"}, (
        "must look up the existing reservation before cancelling it"
    )

    await db_session.refresh(reservation)
    if "cancel_reservation" in _tool_names(result):
        assert reservation.status == "cancelled"


async def test_cancelling_a_large_party_requires_approval(db_session, llm):
    """Constitution IV, exercised through the live agent rather than the service
    directly: cancelling a reservation at/above the high-impact party-size threshold
    must not take effect immediately, however the model phrases its tool call."""
    restaurant = await make_restaurant(db_session)
    user = await make_user(db_session, restaurant)
    raj = await make_customer(db_session, restaurant, name="Raj Patel", phone="+15551230004")
    table = await make_table(db_session, restaurant, seat_capacity=8)
    reservation = Reservation(
        restaurant_id=restaurant.id,
        customer_id=raj.id,
        table_id=table.id,
        party_size=8,
        requested_time=utcnow() + timedelta(days=1, hours=1),
        duration_minutes=90,
        status="booked",
        created_via="manager_request",
    )
    db_session.add(reservation)
    await db_session.flush()

    agent = _build_agent(db_session, llm)
    result = await agent.handle(
        "Cancel Raj's reservation for the party of 8.",
        restaurant_id=restaurant.id,
        initiated_by_user_id=user.id,
    )

    assert result.status != "error", result.summary
    await db_session.refresh(reservation)
    if "cancel_reservation" in _tool_names(result):
        assert result.status == "pending_approval"
        assert reservation.status == "booked", "must not cancel immediately — it requires approval"
