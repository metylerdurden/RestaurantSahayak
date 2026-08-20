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
from app.core.telemetry import get_tracer, start_span
from app.models import Approval
from app.models.approval import APPROVAL_DOMAINS, APPROVAL_RISK_LEVELS
from app.repositories.approval_repo import ApprovalRepository
from app.services.event_bus import EventBus
from app.services.memory_service import MemoryService
from app.tools.base import ToolError, utcnow

Executor = Callable[[Approval], Awaitable[dict[str, Any]]]

_logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class ApprovalService:
    def __init__(
        self,
        repo: ApprovalRepository,
        *,
        executors: dict[str, Executor] | None = None,
        memory_service: MemoryService | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.repo = repo
        self.executors = executors or {}
        self.memory_service = memory_service
        self.event_bus = event_bus

    async def _publish(self, event_type: str, approval: Approval, *, extra: dict[str, Any] | None = None) -> None:
        if self.event_bus is None:
            return
        payload = {
            "approval_id": str(approval.id),
            "domain": approval.domain,
            "action": approval.action,
            "risk_level": approval.risk_level,
            "status": approval.status,
        }
        if extra:
            payload.update(extra)
        await self.event_bus.publish(
            event_type=event_type,
            restaurant_id=approval.restaurant_id,
            entity_id=approval.id,
            payload=payload,
            correlation_id=approval.proposed_by_agent_run_id,
            published_by="approval_service",
        )

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
        # No `parameters`/`reason` span attributes — those may embed operational
        # detail (e.g. a reservation's party size or a memory-derived rationale)
        # beyond the plain identifiers/status telemetry needs.
        with start_span(
            _tracer,
            "approval.create",
            domain=domain,
            agent_name=agent_name,
            restaurant_id=str(restaurant_id),
            risk_level=risk_level,
        ) as span:
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
            approval = await self.repo.create(
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
            await self._publish("approval.created", approval)
            span.set_attribute("approval_id", str(approval.id))
            span.set_attribute("approval_status", approval.status)
            span.set_attribute("success", True)
            return approval

    async def get_pending_approvals(self, restaurant_id: uuid.UUID) -> list[Approval]:
        return await self.repo.list_pending(restaurant_id)

    async def approve(self, approval_id: uuid.UUID, decided_by_user_id: uuid.UUID) -> Approval:
        with start_span(_tracer, "approval.approve", approval_id=str(approval_id)) as span:
            approval = await self._require_pending(approval_id)
            approval.status = "approved"
            approval.decided_by_user_id = decided_by_user_id
            approval.approved_at = utcnow()
            approval = await self.repo.save(approval)

            await self._execute(approval)
            await self._remember(approval)
            if approval.domain == "purchase":
                await self._publish("purchase.approved", approval)
            await self._publish("approval.completed", approval, extra={"decision": "approved"})

            span.set_attribute("domain", approval.domain)
            span.set_attribute("risk_level", approval.risk_level)
            span.set_attribute("approval_status", approval.status)
            execution_status = (approval.execution_result or {}).get("status")
            if execution_status is not None:
                span.set_attribute("execution_status", execution_status)
            span.set_attribute("success", execution_status != "failed")
            return approval

    async def reject(
        self, approval_id: uuid.UUID, decided_by_user_id: uuid.UUID, reason: str | None = None
    ) -> Approval:
        with start_span(_tracer, "approval.reject", approval_id=str(approval_id)) as span:
            approval = await self._require_pending(approval_id)
            approval.status = "rejected"
            approval.decided_by_user_id = decided_by_user_id
            approval.rejected_at = utcnow()
            approval = await self.repo.save(approval)

            await self._remember(approval, note=reason)
            if approval.domain == "purchase":
                await self._publish("purchase.rejected", approval)
            await self._publish("approval.completed", approval, extra={"decision": "rejected"})

            span.set_attribute("domain", approval.domain)
            span.set_attribute("risk_level", approval.risk_level)
            span.set_attribute("approval_status", approval.status)
            span.set_attribute("success", True)
            return approval

    async def expire(self, restaurant_id: uuid.UUID) -> list[Approval]:
        with start_span(_tracer, "approval.expire", restaurant_id=str(restaurant_id)) as span:
            overdue = await self.repo.list_overdue(restaurant_id, utcnow())
            for approval in overdue:
                approval.status = "expired"
                await self.repo.save(approval)
            span.set_attribute("expired_count", len(overdue))
            span.set_attribute("success", True)
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

        # The executor call runs inside a SAVEPOINT: if it fails with a database-
        # level error (not just an application-level exception — e.g. a constraint
        # violation or a lost connection), a plain try/except is not enough. Postgres
        # marks the whole surrounding transaction unusable until it's rolled back, so
        # without this, the very next database write on this session (recording the
        # failure below, or approve()'s event-publish calls right after) would itself
        # raise, masking the fact that the approval decision already succeeded.
        # ROLLBACK TO SAVEPOINT undoes only the executor's own work and leaves the
        # session healthy for everything that follows.
        try:
            async with self.repo.session.begin_nested():
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
            # Same SAVEPOINT rationale as _execute() above: a database-level failure
            # while recording this memory must not poison the session for the
            # _publish() calls (and the request's eventual commit) that follow.
            async with self.repo.session.begin_nested():
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
