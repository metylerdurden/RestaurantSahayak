"""EventEnvelope: the value object handlers/workflows actually receive. Decoupled
from the Event ORM row on purpose — a handler in app.workflows should never need to
touch sqlalchemy or an active session to read an event it's reacting to."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EventType = Literal[
    "reservation.created",
    "reservation.modified",
    "reservation.cancelled",
    "inventory.updated",
    "inventory.low",
    "purchase.requested",
    "purchase.approved",
    "purchase.rejected",
    "customer.updated",
    "staffing.shortage_detected",
    "approval.created",
    "approval.completed",
]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    event_type: EventType
    restaurant_id: uuid.UUID
    entity_id: uuid.UUID | None
    payload: dict[str, Any]
    correlation_id: uuid.UUID
    published_by: str
    created_at: datetime
