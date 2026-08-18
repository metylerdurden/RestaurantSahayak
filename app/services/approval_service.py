"""ApprovalService: the generic propose -> decide state machine behind Constitution
IV. Deliberately domain-agnostic — it knows nothing about reservations, inventory, or
staffing, only about Approval rows. Domain services decide *when* an action is
high-impact and call `propose()`; they alone know how to actually apply their own
action once approved (see e.g. ReservationService.execute_approved_cancellation).

Scope note: automatic re-invocation of an approved action (a generic "run whatever
was captured in `proposed_action`" dispatcher) is Phase 9 work, once the manager-facing
API exists to trigger it. For now, `decide()` manages the Approval state machine only;
callers apply the effect themselves once they see `status == "approved"`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from app.models import Approval
from app.repositories.approval_repo import ApprovalRepository
from app.tools.base import ToolError, utcnow


class ApprovalService:
    def __init__(self, repo: ApprovalRepository) -> None:
        self.repo = repo

    async def propose(
        self,
        *,
        restaurant_id: uuid.UUID,
        domain: Literal["reservation", "inventory", "staffing", "purchase"],
        proposed_by_tool: str,
        proposed_by_agent_run_id: uuid.UUID,
        proposed_action: dict[str, Any],
        summary: str,
        expires_at: datetime | None = None,
    ) -> Approval:
        return await self.repo.create(
            restaurant_id=restaurant_id,
            domain=domain,
            proposed_by_tool=proposed_by_tool,
            proposed_by_agent_run_id=proposed_by_agent_run_id,
            proposed_action=proposed_action,
            summary=summary,
            status="pending",
            expires_at=expires_at,
        )

    async def decide(
        self,
        approval_id: uuid.UUID,
        decision: Literal["approved", "rejected"],
        decided_by_user_id: uuid.UUID,
    ) -> Approval:
        approval = await self.repo.get_by_id(approval_id)
        if approval is None:
            raise ToolError("approval_not_found", f"No approval found with id {approval_id}")
        if approval.status != "pending":
            raise ToolError(
                "approval_already_decided",
                f"Approval {approval_id} is already {approval.status}; decisions are terminal.",
            )
        approval.status = decision
        approval.decided_by_user_id = decided_by_user_id
        approval.decided_at = utcnow()
        return await self.repo.save(approval)

    async def get_pending(self, restaurant_id: uuid.UUID) -> list[Approval]:
        return await self.repo.list_pending(restaurant_id)

    async def expire_overdue(self, restaurant_id: uuid.UUID) -> list[Approval]:
        overdue = await self.repo.list_overdue(restaurant_id, utcnow())
        for approval in overdue:
            approval.status = "expired"
            await self.repo.save(approval)
        return overdue
