"""Reservation tools — the only surface an agent has onto reservation data. Each tool
validates typed input, calls exactly one ReservationService method, and returns typed
output (or a PendingApprovalOutput for a high-impact cancellation/modification)."""

from __future__ import annotations

from app.schemas.reservation import (
    CancelReservationInput,
    CancelReservationOutput,
    CreateReservationInput,
    CreateReservationOutput,
    FindAvailableTableInput,
    FindAvailableTableOutput,
    GetReservationsInput,
    GetReservationsOutput,
    ModifyReservationInput,
    ModifyReservationOutput,
    ReservationDTO,
)
from app.services.reservation_service import ReservationService
from app.tools.base import PendingApprovalOutput, Tool, ToolContext


class GetReservationsTool(Tool[GetReservationsInput, GetReservationsOutput]):
    name = "get_reservations"
    description = (
        "List reservations for the restaurant, optionally filtered by date range, "
        "status, or customer."
    )
    input_model = GetReservationsInput
    output_model = GetReservationsOutput

    def __init__(self, service: ReservationService) -> None:
        self.service = service

    async def run(
        self, input: GetReservationsInput, *, context: ToolContext
    ) -> GetReservationsOutput:
        reservations = await self.service.get_reservations(
            restaurant_id=context.restaurant_id,
            date_from=input.date_from,
            date_to=input.date_to,
            status=input.status,
            customer_id=input.customer_id,
        )
        return GetReservationsOutput(
            reservations=[ReservationDTO.model_validate(r) for r in reservations]
        )


class FindAvailableTableTool(Tool[FindAvailableTableInput, FindAvailableTableOutput]):
    name = "find_available_table"
    description = (
        "Find tables with enough seating capacity and no scheduling conflict for a "
        "given party size and time."
    )
    input_model = FindAvailableTableInput
    output_model = FindAvailableTableOutput

    def __init__(self, service: ReservationService) -> None:
        self.service = service

    async def run(
        self, input: FindAvailableTableInput, *, context: ToolContext
    ) -> FindAvailableTableOutput:
        tables = await self.service.find_available_table(
            restaurant_id=context.restaurant_id,
            party_size=input.party_size,
            requested_time=input.requested_time,
            duration_minutes=input.duration_minutes,
        )
        return FindAvailableTableOutput(
            options=[
                {"id": t.id, "label": t.label, "seat_capacity": t.seat_capacity} for t in tables
            ]
        )


class CreateReservationTool(Tool[CreateReservationInput, CreateReservationOutput]):
    name = "create_reservation"
    description = "Book a new reservation for a customer, party size, and time."
    input_model = CreateReservationInput
    output_model = CreateReservationOutput

    def __init__(self, service: ReservationService) -> None:
        self.service = service

    async def run(
        self, input: CreateReservationInput, *, context: ToolContext
    ) -> CreateReservationOutput:
        reservation = await self.service.create_reservation(
            restaurant_id=context.restaurant_id,
            customer_id=input.customer_id,
            party_size=input.party_size,
            requested_time=input.requested_time,
            table_id=input.table_id,
            duration_minutes=input.duration_minutes,
            notes=input.notes,
            context=context,
        )
        return CreateReservationOutput(reservation=ReservationDTO.model_validate(reservation))


class ModifyReservationTool(Tool[ModifyReservationInput, ModifyReservationOutput]):
    name = "modify_reservation"
    description = (
        "Change an existing reservation's party size, time, table, or notes. "
        "Modifications touching a large party require manager approval."
    )
    input_model = ModifyReservationInput
    output_model = ModifyReservationOutput
    high_impact = True  # conditional — see ReservationService for the actual runtime rule

    def __init__(self, service: ReservationService) -> None:
        self.service = service

    async def run(
        self, input: ModifyReservationInput, *, context: ToolContext
    ) -> ModifyReservationOutput | PendingApprovalOutput:
        result = await self.service.modify_reservation(
            restaurant_id=context.restaurant_id,
            reservation_id=input.reservation_id,
            party_size=input.party_size,
            requested_time=input.requested_time,
            table_id=input.table_id,
            notes=input.notes,
            context=context,
        )
        if isinstance(result, PendingApprovalOutput):
            return result
        return ModifyReservationOutput(reservation=ReservationDTO.model_validate(result))


class CancelReservationTool(Tool[CancelReservationInput, CancelReservationOutput]):
    name = "cancel_reservation"
    description = (
        "Cancel an existing reservation. Cancelling a large party requires manager "
        "approval before it takes effect."
    )
    input_model = CancelReservationInput
    output_model = CancelReservationOutput
    high_impact = True  # conditional — see ReservationService for the actual runtime rule

    def __init__(self, service: ReservationService) -> None:
        self.service = service

    async def run(
        self, input: CancelReservationInput, *, context: ToolContext
    ) -> CancelReservationOutput | PendingApprovalOutput:
        result = await self.service.cancel_reservation(
            restaurant_id=context.restaurant_id,
            reservation_id=input.reservation_id,
            reason=input.reason,
            context=context,
        )
        if isinstance(result, PendingApprovalOutput):
            return result
        return CancelReservationOutput(reservation=ReservationDTO.model_validate(result))
