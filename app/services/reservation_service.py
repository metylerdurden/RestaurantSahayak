"""Reservation domain business rules. Never touches SQL directly — only calls
ReservationRepository/CustomerRepository, and ApprovalService for the high-impact
gate (Constitution IV: cancelling/modifying a large party requires human approval).

Also publishes reservation.created/modified/cancelled to the EventBus (opt-in, like
ApprovalService's executors/memory_service — omitting event_bus at construction time
preserves exact prior behavior) so other parts of the system (e.g. the Inventory
workflow) can react without this service knowing or caring who's listening.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.core.config import Settings
from app.models import Reservation
from app.repositories.customer_repo import CustomerRepository
from app.repositories.reservation_repo import ACTIVE_RESERVATION_STATUSES, ReservationRepository
from app.services.approval_service import ApprovalService
from app.services.event_bus import EventBus
from app.tools.base import PendingApprovalOutput, ToolContext, ToolError, utcnow


class ReservationService:
    def __init__(
        self,
        repo: ReservationRepository,
        customer_repo: CustomerRepository,
        approval_service: ApprovalService,
        settings: Settings,
        event_bus: EventBus | None = None,
    ) -> None:
        self.repo = repo
        self.customer_repo = customer_repo
        self.approval_service = approval_service
        self.settings = settings
        self.event_bus = event_bus

    async def _publish(
        self,
        event_type: str,
        *,
        restaurant_id: uuid.UUID,
        entity_id: uuid.UUID,
        payload: dict[str, Any],
        correlation_id: uuid.UUID | None,
    ) -> None:
        if self.event_bus is None:
            return
        await self.event_bus.publish(
            event_type=event_type,
            restaurant_id=restaurant_id,
            entity_id=entity_id,
            payload=payload,
            correlation_id=correlation_id,
            published_by="reservation_service",
        )

    def _duration(self, requested: int | None) -> int:
        return requested or self.settings.default_reservation_duration_minutes

    def _is_high_impact_party(self, party_size: int) -> bool:
        return party_size >= self.settings.reservation_high_impact_party_size

    async def _require_agent_run(self, context: ToolContext) -> uuid.UUID:
        if context.agent_run_id is None:
            raise ToolError(
                "agent_run_required",
                "A high-impact action must be attributed to an agent run before it can be proposed for approval.",
            )
        return context.agent_run_id

    async def get_reservations(
        self,
        *,
        restaurant_id: uuid.UUID,
        date_from: datetime | None,
        date_to: datetime | None,
        status: str | None,
        customer_id: uuid.UUID | None,
    ) -> list[Reservation]:
        return await self.repo.list(
            restaurant_id, date_from=date_from, date_to=date_to, status=status, customer_id=customer_id
        )

    async def find_available_table(
        self,
        *,
        restaurant_id: uuid.UUID,
        party_size: int,
        requested_time: datetime,
        duration_minutes: int | None,
    ):
        return await self.repo.find_available_tables(
            restaurant_id, party_size, requested_time, self._duration(duration_minutes)
        )

    async def create_reservation(
        self,
        *,
        restaurant_id: uuid.UUID,
        customer_id: uuid.UUID,
        party_size: int,
        requested_time: datetime,
        table_id: uuid.UUID | None,
        duration_minutes: int | None,
        notes: str | None,
        context: ToolContext,
    ) -> Reservation:
        customer = await self.customer_repo.get_by_id(customer_id)
        if customer is None or customer.restaurant_id != restaurant_id:
            raise ToolError("customer_not_found", f"No customer found with id {customer_id}")

        if requested_time <= utcnow():
            raise ToolError("invalid_time", "Requested time must be in the future")

        duration = self._duration(duration_minutes)

        if table_id is not None:
            table = await self.repo.get_table(table_id)
            if table is None or table.restaurant_id != restaurant_id or not table.is_active:
                raise ToolError("table_not_found", f"No active table found with id {table_id}")
            if table.seat_capacity < party_size:
                raise ToolError(
                    "table_too_small",
                    f"Table {table.label} seats {table.seat_capacity}, party is {party_size}",
                )
            if await self.repo.table_has_conflict(table.id, requested_time, duration):
                raise ToolError("table_conflict", f"Table {table.label} is already booked at that time")
            chosen_table_id = table.id
        else:
            candidates = await self.repo.find_available_tables(restaurant_id, party_size, requested_time, duration)
            if not candidates:
                raise ToolError(
                    "no_availability",
                    f"No table available for a party of {party_size} at {requested_time.isoformat()}",
                )
            chosen_table_id = candidates[0].id

        reservation = await self.repo.create(
            restaurant_id=restaurant_id,
            customer_id=customer_id,
            table_id=chosen_table_id,
            party_size=party_size,
            requested_time=requested_time,
            duration_minutes=duration,
            status="booked",
            notes=notes,
            created_via=context.trigger_type,
        )
        await self._publish(
            "reservation.created",
            restaurant_id=restaurant_id,
            entity_id=reservation.id,
            payload={
                "reservation_id": str(reservation.id),
                "customer_id": str(customer_id),
                "table_id": str(chosen_table_id),
                "party_size": party_size,
                "requested_time": requested_time.isoformat(),
            },
            correlation_id=context.agent_run_id,
        )
        return reservation

    async def modify_reservation(
        self,
        *,
        restaurant_id: uuid.UUID,
        reservation_id: uuid.UUID,
        party_size: int | None,
        requested_time: datetime | None,
        table_id: uuid.UUID | None,
        notes: str | None,
        context: ToolContext,
    ) -> Reservation | PendingApprovalOutput:
        reservation = await self.repo.get_by_id(reservation_id)
        if reservation is None or reservation.restaurant_id != restaurant_id:
            raise ToolError("reservation_not_found", f"No reservation found with id {reservation_id}")
        if reservation.status not in ACTIVE_RESERVATION_STATUSES:
            raise ToolError(
                "reservation_not_modifiable",
                f"Reservation is {reservation.status} and cannot be modified",
            )

        effective_party_size = party_size if party_size is not None else reservation.party_size
        high_impact = self._is_high_impact_party(effective_party_size) or self._is_high_impact_party(
            reservation.party_size
        )

        changes: dict = {}
        if party_size is not None:
            changes["party_size"] = party_size
        if requested_time is not None:
            changes["requested_time"] = requested_time.isoformat()
        if table_id is not None:
            changes["table_id"] = str(table_id)
        if notes is not None:
            changes["notes"] = notes

        if high_impact:
            agent_run_id = await self._require_agent_run(context)
            approval = await self.approval_service.create_approval_request(
                restaurant_id=restaurant_id,
                domain="reservation",
                action="modify_reservation",
                agent_name=context.acting_agent,
                proposed_by_agent_run_id=agent_run_id,
                parameters={
                    "action": "modify_reservation",
                    "reservation_id": str(reservation_id),
                    "changes": changes,
                },
                reason=f"Modify reservation for party of {effective_party_size} on {reservation.requested_time.date()}",
                risk_level="MEDIUM",
            )
            return PendingApprovalOutput(approval_id=approval.id, summary=approval.reason)

        await self._apply_modification(reservation, changes)
        reservation.status = "modified"
        reservation = await self.repo.save(reservation)
        await self._publish(
            "reservation.modified",
            restaurant_id=restaurant_id,
            entity_id=reservation.id,
            payload={"reservation_id": str(reservation.id), "changes": changes},
            correlation_id=context.agent_run_id,
        )
        return reservation

    async def cancel_reservation(
        self,
        *,
        restaurant_id: uuid.UUID,
        reservation_id: uuid.UUID,
        reason: str | None,
        context: ToolContext,
    ) -> Reservation | PendingApprovalOutput:
        reservation = await self.repo.get_by_id(reservation_id)
        if reservation is None or reservation.restaurant_id != restaurant_id:
            raise ToolError("reservation_not_found", f"No reservation found with id {reservation_id}")
        if reservation.status not in ACTIVE_RESERVATION_STATUSES:
            raise ToolError(
                "reservation_not_cancellable",
                f"Reservation is already {reservation.status}",
            )

        if self._is_high_impact_party(reservation.party_size):
            agent_run_id = await self._require_agent_run(context)
            approval = await self.approval_service.create_approval_request(
                restaurant_id=restaurant_id,
                domain="reservation",
                action="cancel_reservation",
                agent_name=context.acting_agent,
                proposed_by_agent_run_id=agent_run_id,
                parameters={
                    "action": "cancel_reservation",
                    "reservation_id": str(reservation_id),
                    "reason": reason,
                },
                reason=f"Cancel reservation for party of {reservation.party_size} "
                f"on {reservation.requested_time.date()}",
                risk_level="MEDIUM",
            )
            return PendingApprovalOutput(approval_id=approval.id, summary=approval.reason)

        reservation.status = "cancelled"
        if reason:
            reservation.notes = f"{reservation.notes or ''}\nCancellation reason: {reason}".strip()
        reservation = await self.repo.save(reservation)
        await self._publish(
            "reservation.cancelled",
            restaurant_id=restaurant_id,
            entity_id=reservation.id,
            payload={"reservation_id": str(reservation.id), "reason": reason},
            correlation_id=context.agent_run_id,
        )
        return reservation

    async def _apply_modification(self, reservation: Reservation, changes: dict) -> None:
        new_party_size = changes.get("party_size", reservation.party_size)
        new_time = (
            datetime.fromisoformat(changes["requested_time"])
            if "requested_time" in changes
            else reservation.requested_time
        )
        new_table_id = uuid.UUID(changes["table_id"]) if "table_id" in changes else reservation.table_id

        if new_table_id is not None and (
            "table_id" in changes or "requested_time" in changes or "party_size" in changes
        ):
            table = await self.repo.get_table(new_table_id)
            if table is None or not table.is_active:
                raise ToolError("table_not_found", f"No active table found with id {new_table_id}")
            if table.seat_capacity < new_party_size:
                raise ToolError(
                    "table_too_small",
                    f"Table {table.label} seats {table.seat_capacity}, party is {new_party_size}",
                )
            if await self.repo.table_has_conflict(
                new_table_id, new_time, reservation.duration_minutes, exclude_reservation_id=reservation.id
            ):
                raise ToolError("table_conflict", f"Table {table.label} is already booked at that time")

        reservation.party_size = new_party_size
        reservation.requested_time = new_time
        reservation.table_id = new_table_id
        if "notes" in changes:
            reservation.notes = changes["notes"]

    async def execute_approved_action(self, approval) -> Reservation:
        """Applies an approved reservation action. Invoked automatically by
        ApprovalService.approve() when wired with an executor (see
        app.services.approval_execution.build_executors); still directly callable
        (e.g. by tests, or manually) for a caller that only wants ApprovalService's
        plain decision state machine."""
        action = approval.parameters["action"]
        reservation_id = uuid.UUID(approval.parameters["reservation_id"])
        reservation = await self.repo.get_by_id(reservation_id)
        if reservation is None:
            raise ToolError("reservation_not_found", f"No reservation found with id {reservation_id}")

        if action == "cancel_reservation":
            reservation.status = "cancelled"
            reason = approval.parameters.get("reason")
            if reason:
                reservation.notes = f"{reservation.notes or ''}\nCancellation reason: {reason}".strip()
            event_type = "reservation.cancelled"
            payload: dict = {"reservation_id": str(reservation.id), "reason": reason}
        elif action == "modify_reservation":
            changes = approval.parameters.get("changes", {})
            await self._apply_modification(reservation, changes)
            reservation.status = "modified"
            event_type = "reservation.modified"
            payload = {"reservation_id": str(reservation.id), "changes": changes}
        else:
            raise ToolError("unsupported_action", f"Cannot execute approved action: {action}")

        reservation = await self.repo.save(reservation)
        await self._publish(
            event_type,
            restaurant_id=reservation.restaurant_id,
            entity_id=reservation.id,
            payload=payload,
            correlation_id=approval.proposed_by_agent_run_id,
        )
        return reservation
