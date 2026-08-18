"""Customer tools end-to-end against real Postgres."""

from __future__ import annotations

import uuid

import pytest

from app.repositories.customer_repo import CustomerRepository
from app.services.customer_service import CustomerService
from app.tools.base import ToolContext, ToolError
from app.tools.customer_tools import GetCustomerHistoryTool, GetCustomerTool, UpdateCustomerTool
from tests.integration.factories import make_customer, make_restaurant

pytestmark = pytest.mark.asyncio


async def _build(db_session):
    return CustomerService(CustomerRepository(db_session))


async def test_get_customer_by_id(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant, name="The Patels")
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    output = await GetCustomerTool(service)({"customer_id": str(customer.id)}, context=context)
    assert output.customers[0].name == "The Patels"


async def test_get_customer_by_search_query(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    await make_customer(db_session, restaurant, name="Maria Gonzalez")
    await make_customer(db_session, restaurant, name="David Chen")
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    output = await GetCustomerTool(service)({"query": "Maria"}, context=context)
    assert len(output.customers) == 1
    assert output.customers[0].name == "Maria Gonzalez"


async def test_get_customer_unknown_id_raises_clear_error(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    with pytest.raises(ToolError) as exc_info:
        await GetCustomerTool(service)({"customer_id": str(uuid.uuid4())}, context=context)
    assert exc_info.value.code == "customer_not_found"


async def test_get_customer_history_returns_empty_when_no_reservations(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    output = await GetCustomerHistoryTool(service)({"customer_id": str(customer.id)}, context=context)
    assert output.customer.id == customer.id
    assert output.reservations == []


async def test_update_customer_persists_change(db_session):
    service = await _build(db_session)
    restaurant = await make_restaurant(db_session)
    customer = await make_customer(db_session, restaurant, email=None)
    context = ToolContext(restaurant_id=restaurant.id, correlation_id="c1", acting_agent="customer")

    output = await UpdateCustomerTool(service)(
        {"customer_id": str(customer.id), "email": "new@example.com"}, context=context
    )
    assert output.customer.email == "new@example.com"

    reread = await GetCustomerTool(service)({"customer_id": str(customer.id)}, context=context)
    assert reread.customers[0].email == "new@example.com"
