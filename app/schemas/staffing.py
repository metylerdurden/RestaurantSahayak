from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCDatetime


class StaffDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: str
    is_active: bool


class ShiftAssignmentDTO(BaseModel):
    staff_id: uuid.UUID
    staff_name: str
    staff_role: str


class StaffShiftDTO(BaseModel):
    id: uuid.UUID
    start_at: datetime
    end_at: datetime
    required_staff_count: int
    is_published: bool
    status: str
    assignments: list[ShiftAssignmentDTO]


# --- get_staff_schedule ---


class GetStaffScheduleInput(BaseModel):
    date_from: UTCDatetime
    date_to: UTCDatetime


class GetStaffScheduleOutput(BaseModel):
    shifts: list[StaffShiftDTO]


# --- get_staff_availability ---


class GetStaffAvailabilityInput(BaseModel):
    start_at: UTCDatetime
    end_at: UTCDatetime
    role: str | None = None


class GetStaffAvailabilityOutput(BaseModel):
    available_staff: list[StaffDTO]


# --- calculate_staff_requirement ---


class CalculateStaffRequirementInput(BaseModel):
    start_at: UTCDatetime
    end_at: UTCDatetime
    expected_covers: int | None = Field(default=None, ge=0)


class CalculateStaffRequirementOutput(BaseModel):
    expected_covers: int
    required_servers: int
    required_cooks: int
    required_host: int
    required_total: int
