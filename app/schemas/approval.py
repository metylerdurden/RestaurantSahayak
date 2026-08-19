from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ApprovalDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    restaurant_id: uuid.UUID
    domain: Literal["reservation", "inventory", "staffing", "purchase"]
    action: str
    agent_name: str
    reason: str
    parameters: dict[str, Any]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    status: Literal["pending", "approved", "rejected", "expired"]
    created_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None
    expires_at: datetime | None
    execution_result: dict[str, Any] | None


class DecideApprovalInput(BaseModel):
    decided_by_user_id: uuid.UUID
    reason: str | None = None  # only used by reject
