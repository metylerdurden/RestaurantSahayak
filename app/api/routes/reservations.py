"""Manager API (Step 19): reservations CRUD, called directly against
ReservationService — no LLM in the loop for a simple list/create/modify/cancel from
the dashboard. Mutations still go through every business rule ReservationService
already enforces (conflict checks, the high-impact-party approval gate, ...)
unchanged; they're wrapped in a synthetic "manager_dashboard" AgentRun purely so a
high-impact change has something to attribute its Approval to (see
app.api.manager_context) — the approval gate itself is not touched or bypassed."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.manager_context import complete_manager_run, start_manager_run
from app.api.stack import build_agent_stack
from app.schemas.reservation import ReservationDTO
from app.tools.base import PendingApprovalOutput, ToolError

router = APIRouter(prefix="/api/v1", tags=["reservations"])


class ReservationMutationResponse(BaseModel):
    status: str
    reservation: ReservationDTO | None = None
    approval_id: uuid.UUID | None = None
    summary: str | None = None


class CreateReservationRequest(BaseModel):
    restaurant_id: uuid.UUID
    customer_id: uuid.UUID
    party_size: int = Field(gt=0)
    requested_time: datetime
    table_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    notes: str | None = None
    initiated_by_user_id: uuid.UUID | None = None


class ModifyReservationRequest(BaseModel):
    restaurant_id: uuid.UUID
    party_size: int | None = Field(default=None, gt=0)
    requested_time: datetime | None = None
    table_id: uuid.UUID | None = None
    notes: str | None = None
    initiated_by_user_id: uuid.UUID | None = None


@router.get("/reservations", response_model=list[ReservationDTO])
async def list_reservations(
    restaurant_id: uuid.UUID,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    status: str | None = None,
    customer_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[ReservationDTO]:
    stack = build_agent_stack(session)
    reservations = await stack.reservation_service.get_reservations(
        restaurant_id=restaurant_id, date_from=date_from, date_to=date_to, status=status, customer_id=customer_id
    )
    return [ReservationDTO.model_validate(r) for r in reservations]


@router.post("/reservations", response_model=ReservationMutationResponse)
async def create_reservation(
    body: CreateReservationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ReservationMutationResponse:
    stack = build_agent_stack(session)
    try:
        run, context = await start_manager_run(
            stack.agent_run_service,
            stack.user_repo,
            restaurant_id=body.restaurant_id,
            initiated_by_user_id=body.initiated_by_user_id,
        )
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    try:
        reservation = await stack.reservation_service.create_reservation(
            restaurant_id=body.restaurant_id,
            customer_id=body.customer_id,
            party_size=body.party_size,
            requested_time=body.requested_time,
            table_id=body.table_id,
            duration_minutes=body.duration_minutes,
            notes=body.notes,
            context=context,
        )
    except ToolError as exc:
        await complete_manager_run(stack.agent_run_service, run, status="failed", summary=exc.message)
        raise HTTPException(status_code=422, detail=exc.message) from exc

    await complete_manager_run(
        stack.agent_run_service, run, status="completed", summary=f"Created reservation {reservation.id}"
    )
    return ReservationMutationResponse(status="completed", reservation=ReservationDTO.model_validate(reservation))


@router.patch("/reservations/{reservation_id}", response_model=ReservationMutationResponse)
async def modify_reservation(
    reservation_id: uuid.UUID,
    body: ModifyReservationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ReservationMutationResponse:
    stack = build_agent_stack(session)
    try:
        run, context = await start_manager_run(
            stack.agent_run_service,
            stack.user_repo,
            restaurant_id=body.restaurant_id,
            initiated_by_user_id=body.initiated_by_user_id,
        )
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    try:
        result = await stack.reservation_service.modify_reservation(
            restaurant_id=body.restaurant_id,
            reservation_id=reservation_id,
            party_size=body.party_size,
            requested_time=body.requested_time,
            table_id=body.table_id,
            notes=body.notes,
            context=context,
        )
    except ToolError as exc:
        await complete_manager_run(stack.agent_run_service, run, status="failed", summary=exc.message)
        raise HTTPException(status_code=422, detail=exc.message) from exc

    if isinstance(result, PendingApprovalOutput):
        await complete_manager_run(stack.agent_run_service, run, status="completed", summary=result.summary)
        return ReservationMutationResponse(
            status="pending_approval", approval_id=result.approval_id, summary=result.summary
        )

    await complete_manager_run(
        stack.agent_run_service, run, status="completed", summary=f"Modified reservation {result.id}"
    )
    return ReservationMutationResponse(status="completed", reservation=ReservationDTO.model_validate(result))


@router.delete("/reservations/{reservation_id}", response_model=ReservationMutationResponse)
async def cancel_reservation(
    reservation_id: uuid.UUID,
    restaurant_id: uuid.UUID,
    reason: str | None = None,
    initiated_by_user_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> ReservationMutationResponse:
    stack = build_agent_stack(session)
    try:
        run, context = await start_manager_run(
            stack.agent_run_service,
            stack.user_repo,
            restaurant_id=restaurant_id,
            initiated_by_user_id=initiated_by_user_id,
        )
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    try:
        result = await stack.reservation_service.cancel_reservation(
            restaurant_id=restaurant_id, reservation_id=reservation_id, reason=reason, context=context
        )
    except ToolError as exc:
        await complete_manager_run(stack.agent_run_service, run, status="failed", summary=exc.message)
        raise HTTPException(status_code=422, detail=exc.message) from exc

    if isinstance(result, PendingApprovalOutput):
        await complete_manager_run(stack.agent_run_service, run, status="completed", summary=result.summary)
        return ReservationMutationResponse(
            status="pending_approval", approval_id=result.approval_id, summary=result.summary
        )

    await complete_manager_run(
        stack.agent_run_service, run, status="completed", summary=f"Cancelled reservation {result.id}"
    )
    return ReservationMutationResponse(status="completed", reservation=ReservationDTO.model_validate(result))
