from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReservationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    table_id: uuid.UUID | None
    party_size: int
    requested_time: datetime
    duration_minutes: int
    status: str
    notes: str | None
    created_via: str


class AvailableTableOption(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID = Field(validation_alias="id")
    label: str
    seat_capacity: int


# --- get_reservations ---


class GetReservationsInput(BaseModel):
    date_from: datetime | None = None
    date_to: datetime | None = None
    status: str | None = None
    customer_id: uuid.UUID | None = None


class GetReservationsOutput(BaseModel):
    reservations: list[ReservationDTO]


# --- find_available_table ---


class FindAvailableTableInput(BaseModel):
    party_size: int = Field(gt=0)
    requested_time: datetime
    duration_minutes: int | None = Field(default=None, gt=0)


class FindAvailableTableOutput(BaseModel):
    options: list[AvailableTableOption]


# --- create_reservation ---


class CreateReservationInput(BaseModel):
    customer_id: uuid.UUID
    party_size: int = Field(gt=0)
    requested_time: datetime
    table_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    notes: str | None = None


class CreateReservationOutput(BaseModel):
    reservation: ReservationDTO


# --- modify_reservation ---


class ModifyReservationInput(BaseModel):
    reservation_id: uuid.UUID
    party_size: int | None = Field(default=None, gt=0)
    requested_time: datetime | None = None
    table_id: uuid.UUID | None = None
    notes: str | None = None


class ModifyReservationOutput(BaseModel):
    reservation: ReservationDTO


# --- cancel_reservation ---


class CancelReservationInput(BaseModel):
    reservation_id: uuid.UUID
    reason: str | None = None


class CancelReservationOutput(BaseModel):
    reservation: ReservationDTO
