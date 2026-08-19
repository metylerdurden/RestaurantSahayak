"""Customer domain business rules. Preferences/notes are NOT handled here — those are
Memory records (Constitution III, Phase 6); this service only owns identity/contact
fields on the Customer row itself.
"""

from __future__ import annotations

import uuid

from app.models import Customer, Reservation
from app.repositories.customer_repo import CustomerRepository
from app.services.event_bus import EventBus
from app.tools.base import ToolContext, ToolError


class CustomerService:
    def __init__(self, repo: CustomerRepository, event_bus: EventBus | None = None) -> None:
        self.repo = repo
        self.event_bus = event_bus

    async def get_customer(
        self, *, restaurant_id: uuid.UUID, customer_id: uuid.UUID | None, query: str | None
    ) -> list[Customer]:
        if customer_id is not None:
            customer = await self.repo.get_by_id(customer_id)
            if customer is None or customer.restaurant_id != restaurant_id:
                raise ToolError("customer_not_found", f"No customer found with id {customer_id}")
            return [customer]
        if query:
            return await self.repo.find(restaurant_id, query)
        raise ToolError("missing_search_criteria", "Provide either customer_id or a search query")

    async def get_customer_history(
        self, *, restaurant_id: uuid.UUID, customer_id: uuid.UUID, limit: int
    ) -> tuple[Customer, list[Reservation]]:
        customer = await self.repo.get_by_id(customer_id)
        if customer is None or customer.restaurant_id != restaurant_id:
            raise ToolError("customer_not_found", f"No customer found with id {customer_id}")
        history = await self.repo.list_reservation_history(customer_id, limit=limit)
        return customer, history

    async def update_customer(
        self,
        *,
        restaurant_id: uuid.UUID,
        customer_id: uuid.UUID,
        name: str | None,
        phone: str | None,
        email: str | None,
        context: ToolContext | None = None,
    ) -> Customer:
        customer = await self.repo.get_by_id(customer_id)
        if customer is None or customer.restaurant_id != restaurant_id:
            raise ToolError("customer_not_found", f"No customer found with id {customer_id}")

        # Only non-None fields are ever applied — a field can be changed but never
        # cleared through this tool, so the "at least one contact method" invariant
        # can't be violated here by construction.
        changed: dict[str, str] = {}
        if name is not None:
            customer.name = name
            changed["name"] = name
        if phone is not None:
            customer.phone = phone
            changed["phone"] = phone
        if email is not None:
            customer.email = email
            changed["email"] = email

        customer = await self.repo.save(customer)
        if self.event_bus is not None and changed:
            await self.event_bus.publish(
                event_type="customer.updated",
                restaurant_id=restaurant_id,
                entity_id=customer.id,
                payload={"customer_id": str(customer.id), "changed": changed},
                correlation_id=context.agent_run_id if context else None,
                published_by="customer_service",
            )
        return customer
