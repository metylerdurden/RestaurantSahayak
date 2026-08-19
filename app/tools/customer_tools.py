"""Customer tools — typed surface over CustomerService. Preferences/notes go through
memory tools (Phase 6), not here — this is identity/contact data only."""

from __future__ import annotations

from app.schemas.customer import (
    CustomerDTO,
    GetCustomerHistoryInput,
    GetCustomerHistoryOutput,
    GetCustomerInput,
    GetCustomerOutput,
    ReservationHistoryEntryDTO,
    UpdateCustomerInput,
    UpdateCustomerOutput,
)
from app.services.customer_service import CustomerService
from app.tools.base import Tool, ToolContext


class GetCustomerTool(Tool[GetCustomerInput, GetCustomerOutput]):
    name = "get_customer"
    description = "Look up a customer by id, or search by name/phone/email."
    input_model = GetCustomerInput
    output_model = GetCustomerOutput

    def __init__(self, service: CustomerService) -> None:
        self.service = service

    async def run(self, input: GetCustomerInput, *, context: ToolContext) -> GetCustomerOutput:
        customers = await self.service.get_customer(
            restaurant_id=context.restaurant_id, customer_id=input.customer_id, query=input.query
        )
        return GetCustomerOutput(customers=[CustomerDTO.model_validate(c) for c in customers])


class GetCustomerHistoryTool(Tool[GetCustomerHistoryInput, GetCustomerHistoryOutput]):
    name = "get_customer_history"
    description = "Get a customer's profile plus their recent reservation history."
    input_model = GetCustomerHistoryInput
    output_model = GetCustomerHistoryOutput

    def __init__(self, service: CustomerService) -> None:
        self.service = service

    async def run(
        self, input: GetCustomerHistoryInput, *, context: ToolContext
    ) -> GetCustomerHistoryOutput:
        customer, reservations = await self.service.get_customer_history(
            restaurant_id=context.restaurant_id, customer_id=input.customer_id, limit=input.limit
        )
        return GetCustomerHistoryOutput(
            customer=CustomerDTO.model_validate(customer),
            reservations=[ReservationHistoryEntryDTO.model_validate(r) for r in reservations],
        )


class UpdateCustomerTool(Tool[UpdateCustomerInput, UpdateCustomerOutput]):
    name = "update_customer"
    description = "Update a customer's name, phone, or email."
    input_model = UpdateCustomerInput
    output_model = UpdateCustomerOutput

    def __init__(self, service: CustomerService) -> None:
        self.service = service

    async def run(self, input: UpdateCustomerInput, *, context: ToolContext) -> UpdateCustomerOutput:
        customer = await self.service.update_customer(
            restaurant_id=context.restaurant_id,
            customer_id=input.customer_id,
            name=input.name,
            phone=input.phone,
            email=input.email,
            context=context,
        )
        return UpdateCustomerOutput(customer=CustomerDTO.model_validate(customer))
