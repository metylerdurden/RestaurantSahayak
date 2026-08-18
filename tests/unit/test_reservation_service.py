"""ReservationService business rules against mocked repositories — no database.
Real repository/table-conflict correctness is covered by the integration tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models import Customer, Reservation, Table
from app.repositories.customer_repo import CustomerRepository
from app.repositories.reservation_repo import ReservationRepository
from app.services.approval_service import ApprovalService
from app.services.reservation_service import ReservationService
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError

RESTAURANT_ID = uuid.uuid4()


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x", reservation_high_impact_party_size=6
    )


def _context(agent_run_id: uuid.UUID | None = None) -> ToolContext:
    return ToolContext(
        restaurant_id=RESTAURANT_ID,
        correlation_id="corr-1",
        acting_agent="reservation",
        agent_run_id=agent_run_id,
    )


def _service(repo=None, customer_repo=None, approval_service=None) -> ReservationService:
    return ReservationService(
        repo=repo or AsyncMock(spec=ReservationRepository),
        customer_repo=customer_repo or AsyncMock(spec=CustomerRepository),
        approval_service=approval_service or AsyncMock(spec=ApprovalService),
        settings=_settings(),
    )


@pytest.mark.asyncio
async def test_create_reservation_rejects_unknown_customer():
    customer_repo = AsyncMock(spec=CustomerRepository)
    customer_repo.get_by_id.return_value = None
    service = _service(customer_repo=customer_repo)

    with pytest.raises(ToolError) as exc_info:
        await service.create_reservation(
            restaurant_id=RESTAURANT_ID,
            customer_id=uuid.uuid4(),
            party_size=4,
            requested_time=datetime.now(timezone.utc) + timedelta(days=1),
            table_id=None,
            duration_minutes=None,
            notes=None,
            context=_context(),
        )
    assert exc_info.value.code == "customer_not_found"


@pytest.mark.asyncio
async def test_create_reservation_rejects_past_time():
    customer_repo = AsyncMock(spec=CustomerRepository)
    customer_repo.get_by_id.return_value = Customer(id=uuid.uuid4(), restaurant_id=RESTAURANT_ID, name="C")
    service = _service(customer_repo=customer_repo)

    with pytest.raises(ToolError) as exc_info:
        await service.create_reservation(
            restaurant_id=RESTAURANT_ID,
            customer_id=uuid.uuid4(),
            party_size=4,
            requested_time=datetime.now(timezone.utc) - timedelta(days=1),
            table_id=None,
            duration_minutes=None,
            notes=None,
            context=_context(),
        )
    assert exc_info.value.code == "invalid_time"


@pytest.mark.asyncio
async def test_create_reservation_raises_no_availability_when_no_tables_found():
    customer_repo = AsyncMock(spec=CustomerRepository)
    customer_repo.get_by_id.return_value = Customer(id=uuid.uuid4(), restaurant_id=RESTAURANT_ID, name="C")
    repo = AsyncMock(spec=ReservationRepository)
    repo.find_available_tables.return_value = []
    service = _service(repo=repo, customer_repo=customer_repo)

    with pytest.raises(ToolError) as exc_info:
        await service.create_reservation(
            restaurant_id=RESTAURANT_ID,
            customer_id=uuid.uuid4(),
            party_size=8,
            requested_time=datetime.now(timezone.utc) + timedelta(days=1),
            table_id=None,
            duration_minutes=None,
            notes=None,
            context=_context(),
        )
    assert exc_info.value.code == "no_availability"


@pytest.mark.asyncio
async def test_create_reservation_rejects_explicit_table_too_small():
    customer_repo = AsyncMock(spec=CustomerRepository)
    customer_repo.get_by_id.return_value = Customer(id=uuid.uuid4(), restaurant_id=RESTAURANT_ID, name="C")
    repo = AsyncMock(spec=ReservationRepository)
    repo.get_table.return_value = Table(
        id=uuid.uuid4(), restaurant_id=RESTAURANT_ID, label="T1", seat_capacity=2, is_active=True
    )
    service = _service(repo=repo, customer_repo=customer_repo)

    with pytest.raises(ToolError) as exc_info:
        await service.create_reservation(
            restaurant_id=RESTAURANT_ID,
            customer_id=uuid.uuid4(),
            party_size=6,
            requested_time=datetime.now(timezone.utc) + timedelta(days=1),
            table_id=uuid.uuid4(),
            duration_minutes=None,
            notes=None,
            context=_context(),
        )
    assert exc_info.value.code == "table_too_small"


@pytest.mark.asyncio
async def test_cancel_small_party_executes_immediately_without_approval():
    reservation_id = uuid.uuid4()
    repo = AsyncMock(spec=ReservationRepository)
    repo.get_by_id.return_value = Reservation(
        id=reservation_id,
        restaurant_id=RESTAURANT_ID,
        customer_id=uuid.uuid4(),
        party_size=2,
        requested_time=datetime.now(timezone.utc) + timedelta(days=1),
        duration_minutes=90,
        status="booked",
        created_via="manager_request",
    )
    repo.save.side_effect = lambda r: r
    approval_service = AsyncMock(spec=ApprovalService)
    service = _service(repo=repo, approval_service=approval_service)

    result = await service.cancel_reservation(
        restaurant_id=RESTAURANT_ID, reservation_id=reservation_id, reason=None, context=_context()
    )

    assert isinstance(result, Reservation)
    assert result.status == "cancelled"
    approval_service.propose.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_large_party_requires_approval_and_does_not_mutate():
    reservation_id = uuid.uuid4()
    reservation = Reservation(
        id=reservation_id,
        restaurant_id=RESTAURANT_ID,
        customer_id=uuid.uuid4(),
        party_size=8,  # >= default threshold of 6
        requested_time=datetime.now(timezone.utc) + timedelta(days=1),
        duration_minutes=90,
        status="booked",
        created_via="manager_request",
    )
    repo = AsyncMock(spec=ReservationRepository)
    repo.get_by_id.return_value = reservation
    approval_service = AsyncMock(spec=ApprovalService)
    approval_id = uuid.uuid4()
    approval_service.propose.return_value = type(
        "FakeApproval", (), {"id": approval_id, "summary": "Cancel reservation for party of 8"}
    )()
    service = _service(repo=repo, approval_service=approval_service)

    result = await service.cancel_reservation(
        restaurant_id=RESTAURANT_ID,
        reservation_id=reservation_id,
        reason="plans changed",
        context=_context(agent_run_id=uuid.uuid4()),
    )

    assert isinstance(result, PendingApprovalOutput)
    assert result.approval_id == approval_id
    assert reservation.status == "booked"  # unchanged — high-impact tools must not mutate
    repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_large_party_without_agent_run_raises_clear_error():
    reservation_id = uuid.uuid4()
    repo = AsyncMock(spec=ReservationRepository)
    repo.get_by_id.return_value = Reservation(
        id=reservation_id,
        restaurant_id=RESTAURANT_ID,
        customer_id=uuid.uuid4(),
        party_size=10,
        requested_time=datetime.now(timezone.utc) + timedelta(days=1),
        duration_minutes=90,
        status="booked",
        created_via="manager_request",
    )
    service = _service(repo=repo)

    with pytest.raises(ToolError) as exc_info:
        await service.cancel_reservation(
            restaurant_id=RESTAURANT_ID, reservation_id=reservation_id, reason=None, context=_context()
        )
    assert exc_info.value.code == "agent_run_required"


@pytest.mark.asyncio
async def test_cancel_already_cancelled_reservation_is_rejected():
    reservation_id = uuid.uuid4()
    repo = AsyncMock(spec=ReservationRepository)
    repo.get_by_id.return_value = Reservation(
        id=reservation_id,
        restaurant_id=RESTAURANT_ID,
        customer_id=uuid.uuid4(),
        party_size=2,
        requested_time=datetime.now(timezone.utc) + timedelta(days=1),
        duration_minutes=90,
        status="cancelled",
        created_via="manager_request",
    )
    service = _service(repo=repo)

    with pytest.raises(ToolError) as exc_info:
        await service.cancel_reservation(
            restaurant_id=RESTAURANT_ID, reservation_id=reservation_id, reason=None, context=_context()
        )
    assert exc_info.value.code == "reservation_not_cancellable"
