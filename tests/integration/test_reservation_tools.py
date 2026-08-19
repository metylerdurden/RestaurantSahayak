"""Reservation tools end-to-end against real Postgres: Tool -> Service -> Repository
-> SQLAlchemy -> DB, including the full high-impact propose -> approve -> execute
round trip for cancel_reservation."""

from __future__ import annotations

import uuid

import pytest

from app.repositories.approval_repo import ApprovalRepository
from app.repositories.customer_repo import CustomerRepository
from app.repositories.reservation_repo import ReservationRepository
from app.services.approval_service import ApprovalService
from app.services.reservation_service import ReservationService
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError
from app.tools.reservation_tools import (
    CancelReservationTool,
    CreateReservationTool,
    FindAvailableTableTool,
    GetReservationsTool,
    ModifyReservationTool,
)
from tests.integration.factories import make_agent_run, make_customer, make_restaurant, make_table, make_user, future

pytestmark = pytest.mark.asyncio


async def _build(db_session):
    from app.core.config import get_settings

    repo = ReservationRepository(db_session)
    customer_repo = CustomerRepository(db_session)
    approval_repo = ApprovalRepository(db_session)
    approval_service = ApprovalService(approval_repo)
    service = ReservationService(repo, customer_repo, approval_service, get_settings())
    return service, approval_service


async def test_find_available_table_returns_smallest_fitting_table(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    await make_table(db_session, restaurant, seat_capacity=2, label="T1")
    medium = await make_table(db_session, restaurant, seat_capacity=4, label="T2")
    await make_table(db_session, restaurant, seat_capacity=8, label="T3")

    tool = FindAvailableTableTool(service)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation")
    output = await tool({"party_size": 3, "requested_time": future().isoformat()}, context=context)

    assert [o.table_id for o in output.options][0] == medium.id


async def test_create_reservation_end_to_end(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    await make_table(db_session, restaurant, seat_capacity=4)

    tool = CreateReservationTool(service)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation")
    output = await tool(
        {"customer_id": str(customer.id), "party_size": 4, "requested_time": future().isoformat()},
        context=context,
    )

    assert output.reservation.status == "booked"
    assert output.reservation.customer_id == customer.id


async def test_create_reservation_conflict_raises_clear_error_via_explicit_table(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=4)
    tool = CreateReservationTool(service)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation")
    when = future()

    await tool(
        {
            "customer_id": str(customer.id),
            "party_size": 4,
            "requested_time": when.isoformat(),
            "table_id": str(table.id),
        },
        context=context,
    )

    with pytest.raises(ToolError) as exc_info:
        await tool(
            {
                "customer_id": str(customer.id),
                "party_size": 4,
                "requested_time": when.isoformat(),
                "table_id": str(table.id),
            },
            context=context,
        )
    assert exc_info.value.code == "table_conflict"


async def test_get_reservations_filters_by_status(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    await make_table(db_session, restaurant, seat_capacity=4)
    create_tool = CreateReservationTool(service)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation")
    await create_tool(
        {"customer_id": str(customer.id), "party_size": 4, "requested_time": future().isoformat()},
        context=context,
    )

    get_tool = GetReservationsTool(service)
    output = await get_tool({"status": "booked"}, context=context)
    assert len(output.reservations) == 1

    output_empty = await get_tool({"status": "cancelled"}, context=context)
    assert len(output_empty.reservations) == 0


async def test_cancel_small_party_via_tool_executes_immediately(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    await make_table(db_session, restaurant, seat_capacity=2)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation")

    create_output = await CreateReservationTool(service)(
        {"customer_id": str(customer.id), "party_size": 2, "requested_time": future().isoformat()},
        context=context,
    )

    cancel_output = await CancelReservationTool(service)(
        {"reservation_id": str(create_output.reservation.id)}, context=context
    )
    assert cancel_output.reservation.status == "cancelled"


async def test_cancel_large_party_full_approval_round_trip(db_session):
    service, approval_service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id,
        correlation_id="c1",
        acting_agent="reservation",
        agent_run_id=agent_run.id,
    )
    create_output = await CreateReservationTool(service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()},
        context=context,
    )
    reservation_id = create_output.reservation.id

    cancel_result = await CancelReservationTool(service)(
        {"reservation_id": str(reservation_id), "reason": "no longer needed"}, context=context
    )
    assert isinstance(cancel_result, PendingApprovalOutput)

    # Reservation must NOT have changed yet.
    get_output = await GetReservationsTool(service)({}, context=context)
    assert get_output.reservations[0].status == "booked"

    # Reject path: should leave the reservation untouched and be terminal.
    approval = await approval_service.reject(cancel_result.approval_id, user.id)
    assert approval.status == "rejected"
    with pytest.raises(ToolError):
        await approval_service.approve(cancel_result.approval_id, user.id)

    get_output_after_reject = await GetReservationsTool(service)({}, context=context)
    assert get_output_after_reject.reservations[0].status == "booked"


async def test_cancel_large_party_approved_actually_cancels(db_session):
    service, approval_service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    user = await make_user(db_session, restaurant)
    agent_run = await make_agent_run(db_session, restaurant, user)
    await make_table(db_session, restaurant, seat_capacity=8)

    context = ToolContext(
        restaurant_id=restaurant.id,
        correlation_id="c1",
        acting_agent="reservation",
        agent_run_id=agent_run.id,
    )
    create_output = await CreateReservationTool(service)(
        {"customer_id": str(customer.id), "party_size": 8, "requested_time": future().isoformat()},
        context=context,
    )

    cancel_result = await CancelReservationTool(service)(
        {"reservation_id": str(create_output.reservation.id)}, context=context
    )
    approval = await approval_service.approve(cancel_result.approval_id, user.id)
    assert approval.status == "approved"

    reservation = await service.execute_approved_action(approval)
    assert reservation.status == "cancelled"


async def test_modify_small_party_executes_immediately(db_session):
    service, _ = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    await make_table(db_session, restaurant, seat_capacity=6, label="Big")
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="reservation")

    create_output = await CreateReservationTool(service)(
        {"customer_id": str(customer.id), "party_size": 2, "requested_time": future().isoformat()},
        context=context,
    )

    modify_output = await ModifyReservationTool(service)(
        {"reservation_id": str(create_output.reservation.id), "party_size": 3}, context=context
    )
    assert modify_output.reservation.status == "modified"
    assert modify_output.reservation.party_size == 3
