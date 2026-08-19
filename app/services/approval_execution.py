"""Builds the executor registry ApprovalService.approve() uses to actually apply an
approved action, keyed by Approval.domain. Kept separate from ApprovalService itself
so that service stays domain-agnostic — this module is the one place that knows
"reservation" means ReservationService.execute_approved_action, serialized through
ReservationDTO, and so on.
"""

from __future__ import annotations

from typing import Any

from app.models import Approval
from app.schemas.inventory import PurchaseRequestDTO
from app.schemas.reservation import ReservationDTO
from app.services.approval_service import Executor
from app.services.inventory_service import InventoryService
from app.services.reservation_service import ReservationService


def build_executors(
    *,
    reservation_service: ReservationService | None = None,
    inventory_service: InventoryService | None = None,
) -> dict[str, Executor]:
    executors: dict[str, Executor] = {}

    if reservation_service is not None:

        async def _execute_reservation(approval: Approval) -> dict[str, Any]:
            reservation = await reservation_service.execute_approved_action(approval)
            return ReservationDTO.model_validate(reservation).model_dump(mode="json")

        executors["reservation"] = _execute_reservation

    if inventory_service is not None:

        async def _execute_purchase(approval: Approval) -> dict[str, Any]:
            purchase_request = await inventory_service.execute_approved_action(approval)
            return PurchaseRequestDTO.model_validate(purchase_request).model_dump(mode="json")

        executors["purchase"] = _execute_purchase

    return executors
