"""ApprovalService: the human-approval gate behind Constitution IV.

    Agent proposes action -> create_approval_request() -> PENDING -> Manager decides
    -> approve()/reject() -> [if approved] execute registered executor -> record
    execution_result -> store a PAST_DECISION memory -> terminal.

Deliberately domain-agnostic — it knows nothing about reservations, inventory, or
staffing. Domain services (ReservationService, InventoryService, ...) decide *when*
an action is high-impact enough to need approval and what its risk_level is, then
call `create_approval_request()`; they alone know how to actually apply their own
action once approved (see e.g. ReservationService.execute_approved_action).

Execution and memory are opt-in, via the `executors`/`memory_service` constructor
parameters. Omitting them preserves the plain propose/decide state machine this
service always had — every pre-existing caller that only needs that keeps working
unchanged. Providing them turns approve() into the full pipeline: execute the
approved action through the domain's own executor, persist what happened, and record
it as a memory. Either way, nothing except approve() ever executes a gated action —
a tool that decided its action was high-impact already returned a PendingApprovalOutput
instead of mutating anything (see ReservationService.cancel_reservation for the
pattern every gated tool follows), so there is no code path back to that tool that
skips this gate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from app.core.logging import get_logger
from app.models import Approval
from app.models.approval import APPROVAL_DOMAINS, APPROVAL_RISK_LEVELS
from app.repositories.approval_repo import ApprovalRepository
from app.services.memory_service import MemoryService
from app.tools.base import ToolError, utcnow

Executor = Callable[[Approval], Awaitable[dict[str, Any]]]

_logger = get_logger(__name__)


class ApprovalService:
    def __init__(
        self,
        repo: ApprovalRepository,
        *,
        executors: dict[str, Executor] | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.repo = repo
        self.executors = executors or {}
        self.memory_service = memory_service

    async def create_approval_request(
        self,
        *,
        restaurant_id: uuid.UUID,
        domain: Literal["reservation", "inventory", "staffing", "purchase"],
        action: str,
        agent_name: str,
        proposed_by_agent_run_id: uuid.UUID,
        parameters: dict[str, Any],
        reason: str,
        risk_level: Literal["LOW", "MEDIUM", "HIGH"],
        expires_at: datetime | None = None,
    ) -> Approval:
        if domain not in APPROVAL_DOMAINS:
            raise ToolError("invalid_domain", f"Unknown approval domain: {domain!r}")
        if risk_level not in APPROVAL_RISK_LEVELS:
            raise ToolError("invalid_risk_level", f"Unknown risk_level: {risk_level!r}")
        if risk_level == "LOW":
            # LOW-risk actions (reads, recommendations, ...) never go through approval
            # at all — a caller reaching here for one is a mismatch worth catching
            # explicitly rather than silently creating a needless approval record.
            raise ToolError(
                "low_risk_does_not_require_approval",
                "LOW-risk actions execute automatically and must not create an approval request.",
            )
        return await self.repo.create(
            restaurant_id=restaurant_id,
            domain=domain,
            action=action,
            agent_name=agent_name,
            proposed_by_agent_run_id=proposed_by_agent_run_id,
            parameters=parameters,
            reason=reason,
            risk_level=risk_level,
            status="pending",
            expires_at=expires_at,
        )

    async def get_pending_approvals(self, restaurant_id: uuid.UUID) -> list[Approval]:
        return await self.repo.list_pending(restaurant_id)

    async def approve(self, approval_id: uuid.UUID, decided_by_user_id: uuid.UUID) -> Approval:
        approval = await self._require_pending(approval_id)
        approval.status = "approved"
        approval.decided_by_user_id = decided_by_user_id
        approval.approved_at = utcnow()
        approval = await self.repo.save(approval)

        await self._execute(approval)
        await self._remember(approval)
        return approval

    async def reject(
        self, approval_id: uuid.UUID, decided_by_user_id: uuid.UUID, reason: str | None = None
    ) -> Approval:
        approval = await self._require_pending(approval_id)
        approval.status = "rejected"
        approval.decided_by_user_id = decided_by_user_id
        approval.rejected_at = utcnow()
        approval = await self.repo.save(approval)

        await self._remember(approval, note=reason)
        return approval

    async def expire(self, restaurant_id: uuid.UUID) -> list[Approval]:
        overdue = await self.repo.list_overdue(restaurant_id, utcnow())
        for approval in overdue:
            approval.status = "expired"
            await self.repo.save(approval)
        return overdue

    # --- helpers ---

    async def _require_pending(self, approval_id: uuid.UUID) -> Approval:
        approval = await self.repo.get_by_id(approval_id)
        if approval is None:
            raise ToolError("approval_not_found", f"No approval found with id {approval_id}")
        if approval.status == "expired":
            raise ToolError(
                "approval_expired",
                f"Approval {approval_id} has expired and can no longer be approved or rejected.",
            )
        if approval.status != "pending":
            raise ToolError(
                "approval_already_decided",
                f"Approval {approval_id} is already {approval.status}; decisions are terminal.",
            )
        return approval

    async def _execute(self, approval: Approval) -> None:
        executor = self.executors.get(approval.domain)
        if executor is None:
            # No executor wired for this domain — the decision is recorded; applying
            # it is left to a manual execute_approved_action() call, same as before
            # this feature existed.
            return

        try:
            result = await executor(approval)
            approval.execution_result = {"status": "success", "result": result}
        except Exception as exc:
            _logger.warning(
                "approval.execution_failed", approval_id=str(approval.id), domain=approval.domain, error=str(exc)
            )
            approval.execution_result = {"status": "failed", "error": str(exc)}
        approval.executed_at = utcnow()
        await self.repo.save(approval)

    async def _remember(self, approval: Approval, *, note: str | None = None) -> None:
        if self.memory_service is None:
            return

        text = (
            f"{approval.agent_name} proposed to {approval.reason} (risk: {approval.risk_level}). "
            f"Manager {approval.status} this request."
        )
        if approval.execution_result is not None:
            text += f" Execution: {approval.execution_result.get('status')}."
        if note:
            text += f" Note: {note}"

        try:
            await self.memory_service.add_memory(
                restaurant_id=approval.restaurant_id,
                agent_name=approval.agent_name,
                memory_type="PAST_DECISION",
                # Unique per approval — each past decision is its own historical
                # fact, not a "current truth" that should supersede prior ones the
                # way a customer preference does.
                topic=f"approval_{approval.id}",
                content={"text": text},
                source="manager_stated",
                importance=5 if approval.risk_level == "HIGH" else 3,
                confidence=1.0,
                source_agent_run_id=approval.proposed_by_agent_run_id,
            )
        except Exception as exc:
            # Memory recording is observability, not the primary effect — it must
            # never undo or mask a real approval decision/execution that already happened.
            _logger.warning("approval.memory_recording_failed", approval_id=str(approval.id), error=str(exc))
