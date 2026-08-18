from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    is_active: bool


class ReservationHistoryEntryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requested_time: datetime
    party_size: int
    status: str


# --- get_customer ---


class GetCustomerInput(BaseModel):
    customer_id: uuid.UUID | None = None
    query: str | None = None  # name/phone/email search, used when customer_id is absent


class GetCustomerOutput(BaseModel):
    customers: list[CustomerDTO]


# --- get_customer_history ---


class GetCustomerHistoryInput(BaseModel):
    customer_id: uuid.UUID
    limit: int = Field(default=20, gt=0, le=100)


class GetCustomerHistoryOutput(BaseModel):
    customer: CustomerDTO
    reservations: list[ReservationHistoryEntryDTO]


# --- update_customer ---


class UpdateCustomerInput(BaseModel):
    customer_id: uuid.UUID
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class UpdateCustomerOutput(BaseModel):
    customer: CustomerDTO
