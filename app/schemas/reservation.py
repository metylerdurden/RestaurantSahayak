from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _assume_utc(value: datetime) -> datetime:
    """A live LLM producing tool-call arguments routinely omits a UTC offset (e.g.
    "2026-08-18T20:00" instead of "...+00:00") even when told the current time is
    UTC. Treat a naive datetime as UTC rather than raising deep inside comparison
    logic (`naive <= aware` is a TypeError, not a clean ToolError)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


UTCDatetime = Annotated[datetime, AfterValidator(_assume_utc)]


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
    date_from: UTCDatetime | None = None
    date_to: UTCDatetime | None = None
    status: str | None = None
    customer_id: uuid.UUID | None = None


class GetReservationsOutput(BaseModel):
    reservations: list[ReservationDTO]


# --- find_available_table ---


class FindAvailableTableInput(BaseModel):
    party_size: int = Field(gt=0)
    requested_time: UTCDatetime
    duration_minutes: int | None = Field(default=None, gt=0)


class FindAvailableTableOutput(BaseModel):
    options: list[AvailableTableOption]


# --- create_reservation ---


class CreateReservationInput(BaseModel):
    customer_id: uuid.UUID
    party_size: int = Field(gt=0)
    requested_time: UTCDatetime
    table_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    notes: str | None = None


class CreateReservationOutput(BaseModel):
    reservation: ReservationDTO


# --- modify_reservation ---


class ModifyReservationInput(BaseModel):
    reservation_id: uuid.UUID
    party_size: int | None = Field(default=None, gt=0)
    requested_time: UTCDatetime | None = None
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
