"""Staffing tools end-to-end against real Postgres."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import ShiftAssignment, StaffShift
from app.repositories.staffing_repo import StaffingRepository
from app.services.staffing_service import StaffingService
from app.tools.base import ToolContext, utcnow
from app.tools.staffing_tools import (
    CalculateStaffRequirementTool,
    GetStaffAvailabilityTool,
    GetStaffScheduleTool,
)
from tests.integration.factories import make_restaurant, make_staff

pytestmark = pytest.mark.asyncio


async def _build(db_session):
    return StaffingService(StaffingRepository(db_session), _test_settings())


def _test_settings():
    from app.core.config import get_settings

    return get_settings()


async def test_get_staff_schedule_includes_assignments(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    staff = await make_staff(db_session, restaurant, role="server", name="Alex")
    start = utcnow() + timedelta(days=1)
    shift = StaffShift(
        restaurant_id=restaurant.id,
        start_at=start,
        end_at=start + timedelta(hours=4),
        required_staff_count=2,
        status="understaffed",
    )
    db_session.add(shift)
    await db_session.flush()
    db_session.add(ShiftAssignment(shift_id=shift.id, staff_id=staff.id))
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="staffing")
    output = await GetStaffScheduleTool(service)(
        {"date_from": start.isoformat(), "date_to": (start + timedelta(days=2)).isoformat()},
        context=context,
    )
    assert len(output.shifts) == 1
    assert output.shifts[0].assignments[0].staff_name == "Alex"
    assert output.shifts[0].status == "understaffed"


async def test_get_staff_availability_excludes_assigned_staff(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    await make_staff(db_session, restaurant, role="server", name="Free")
    busy_staff = await make_staff(db_session, restaurant, role="server", name="Busy")
    start = utcnow() + timedelta(days=1)
    shift = StaffShift(
        restaurant_id=restaurant.id,
        start_at=start,
        end_at=start + timedelta(hours=4),
        required_staff_count=1,
    )
    db_session.add(shift)
    await db_session.flush()
    db_session.add(ShiftAssignment(shift_id=shift.id, staff_id=busy_staff.id))
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="staffing")
    output = await GetStaffAvailabilityTool(service)(
        {"start_at": start.isoformat(), "end_at": (start + timedelta(hours=4)).isoformat()},
        context=context,
    )
    names = {s.name for s in output.available_staff}
    assert "Free" in names
    assert "Busy" not in names


async def test_calculate_staff_requirement_from_real_reservations(db_session):
    from app.models import Reservation
    from tests.integration.factories import make_customer, make_table

    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    table = await make_table(db_session, restaurant, seat_capacity=8)
    start = utcnow() + timedelta(days=1)
    end = start + timedelta(hours=5)

    for party_size in (4, 6):
        db_session.add(
            Reservation(
                restaurant_id=restaurant.id,
                customer_id=customer.id,
                table_id=table.id,
                party_size=party_size,
                requested_time=start + timedelta(hours=1),
                status="booked",
                created_via="manager_request",
            )
        )
    await db_session.flush()

    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="staffing")
    output = await CalculateStaffRequirementTool(service)(
        {"start_at": start.isoformat(), "end_at": end.isoformat()}, context=context
    )
    assert output.expected_covers == 10
    assert output.required_total == output.required_servers + output.required_cooks + output.required_host
