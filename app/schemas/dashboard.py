from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.approval import ApprovalDTO
from app.schemas.event import EventType
from app.schemas.inventory import InventoryItemDTO
from app.schemas.reservation import ReservationDTO
from app.schemas.staffing import StaffShiftDTO
from app.schemas.workflow_run import AgentRunSummaryDTO


class EventSummaryDTO(BaseModel):
    event_id: uuid.UUID
    event_type: EventType
    entity_id: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime
    handled: bool


class DashboardResponse(BaseModel):
    restaurant_id: uuid.UUID
    generated_at: datetime
    manager_user_id: uuid.UUID | None

    today_reservations: list[ReservationDTO]
    expected_covers: int

    inventory_alerts: list[InventoryItemDTO]
    staffing_alerts: list[StaffShiftDTO]
    pending_approvals: list[ApprovalDTO]

    recent_agent_activity: list[AgentRunSummaryDTO]

    daily_briefing: str | None
    daily_briefing_generated_at: datetime | None

    recent_events: list[EventSummaryDTO]
