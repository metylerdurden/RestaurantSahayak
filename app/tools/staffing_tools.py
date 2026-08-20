"""Staffing tools — typed surface over StaffingService."""

from __future__ import annotations

from app.schemas.staffing import (
    CalculateStaffRequirementInput,
    CalculateStaffRequirementOutput,
    GetStaffAvailabilityInput,
    GetStaffAvailabilityOutput,
    GetStaffScheduleInput,
    GetStaffScheduleOutput,
    ShiftAssignmentDTO,
    StaffDTO,
    StaffShiftDTO,
)
from app.services.staffing_service import StaffingService
from app.tools.base import Tool, ToolContext


class GetStaffScheduleTool(Tool[GetStaffScheduleInput, GetStaffScheduleOutput]):
    name = "get_staff_schedule"
    description = (
        "Get the shift schedule (with staff assignments) for a date range. date_from "
        "and date_to are whole calendar days — any time-of-day given is ignored and "
        "the entire day is included, so a bare date like 2026-08-20 for both is "
        "enough to get everything scheduled that day."
    )
    input_model = GetStaffScheduleInput
    output_model = GetStaffScheduleOutput

    def __init__(self, service: StaffingService) -> None:
        self.service = service

    async def run(self, input: GetStaffScheduleInput, *, context: ToolContext) -> GetStaffScheduleOutput:
        rows = await self.service.get_staff_schedule(
            restaurant_id=context.restaurant_id, date_from=input.date_from, date_to=input.date_to
        )
        shifts = [
            StaffShiftDTO(
                id=shift.id,
                start_at=shift.start_at,
                end_at=shift.end_at,
                required_staff_count=shift.required_staff_count,
                is_published=shift.is_published,
                status=shift.status,
                assignments=[
                    ShiftAssignmentDTO(staff_id=staff.id, staff_name=staff.name, staff_role=staff.role)
                    for staff, _assignment in assignments
                ],
            )
            for shift, assignments in rows
        ]
        return GetStaffScheduleOutput(shifts=shifts)


class GetStaffAvailabilityTool(Tool[GetStaffAvailabilityInput, GetStaffAvailabilityOutput]):
    name = "get_staff_availability"
    description = "List staff with no shift assignment overlapping a given time window."
    input_model = GetStaffAvailabilityInput
    output_model = GetStaffAvailabilityOutput

    def __init__(self, service: StaffingService) -> None:
        self.service = service

    async def run(self, input: GetStaffAvailabilityInput, *, context: ToolContext) -> GetStaffAvailabilityOutput:
        staff = await self.service.get_staff_availability(
            restaurant_id=context.restaurant_id,
            start_at=input.start_at,
            end_at=input.end_at,
            role=input.role,
        )
        return GetStaffAvailabilityOutput(available_staff=[StaffDTO.model_validate(s) for s in staff])


class CalculateStaffRequirementTool(Tool[CalculateStaffRequirementInput, CalculateStaffRequirementOutput]):
    name = "calculate_staff_requirement"
    description = (
        "Estimate how many servers/cooks are needed for a shift, based on expected "
        "covers (or existing reservations if not provided)."
    )
    input_model = CalculateStaffRequirementInput
    output_model = CalculateStaffRequirementOutput

    def __init__(self, service: StaffingService) -> None:
        self.service = service

    async def run(
        self, input: CalculateStaffRequirementInput, *, context: ToolContext
    ) -> CalculateStaffRequirementOutput:
        covers, servers, cooks, host, total = await self.service.calculate_staff_requirement(
            restaurant_id=context.restaurant_id,
            start_at=input.start_at,
            end_at=input.end_at,
            expected_covers=input.expected_covers,
        )
        return CalculateStaffRequirementOutput(
            expected_covers=covers,
            required_servers=servers,
            required_cooks=cooks,
            required_host=host,
            required_total=total,
        )
