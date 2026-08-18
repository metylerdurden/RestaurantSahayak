"""CustomerService business rules against mocked repository — no database."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.models import Customer
from app.repositories.customer_repo import CustomerRepository
from app.services.customer_service import CustomerService
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


def _customer(**overrides) -> Customer:
    defaults = dict(id=uuid.uuid4(), restaurant_id=RESTAURANT_ID, name="Jane Doe", phone="+15551234567")
    defaults.update(overrides)
    return Customer(**defaults)


@pytest.mark.asyncio
async def test_get_customer_by_id_raises_when_not_found():
    repo = AsyncMock(spec=CustomerRepository)
    repo.get_by_id.return_value = None
    service = CustomerService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.get_customer(restaurant_id=RESTAURANT_ID, customer_id=uuid.uuid4(), query=None)
    assert exc_info.value.code == "customer_not_found"


@pytest.mark.asyncio
async def test_get_customer_requires_id_or_query():
    repo = AsyncMock(spec=CustomerRepository)
    service = CustomerService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.get_customer(restaurant_id=RESTAURANT_ID, customer_id=None, query=None)
    assert exc_info.value.code == "missing_search_criteria"


@pytest.mark.asyncio
async def test_get_customer_by_query_returns_matches():
    repo = AsyncMock(spec=CustomerRepository)
    repo.find.return_value = [_customer(), _customer()]
    service = CustomerService(repo)

    result = await service.get_customer(restaurant_id=RESTAURANT_ID, customer_id=None, query="Jane")
    assert len(result) == 2


@pytest.mark.asyncio
async def test_update_customer_only_applies_provided_fields():
    repo = AsyncMock(spec=CustomerRepository)
    customer = _customer(email=None)
    repo.get_by_id.return_value = customer
    repo.save.side_effect = lambda c: c
    service = CustomerService(repo)

    updated = await service.update_customer(
        restaurant_id=RESTAURANT_ID, customer_id=customer.id, name=None, phone=None, email="jane@example.com"
    )
    assert updated.name == "Jane Doe"  # unchanged
    assert updated.email == "jane@example.com"


@pytest.mark.asyncio
async def test_update_customer_wrong_restaurant_raises():
    repo = AsyncMock(spec=CustomerRepository)
    repo.get_by_id.return_value = _customer(restaurant_id=uuid.uuid4())
    service = CustomerService(repo)

    with pytest.raises(ToolError) as exc_info:
        await service.update_customer(
            restaurant_id=RESTAURANT_ID, customer_id=uuid.uuid4(), name="X", phone=None, email=None
        )
    assert exc_info.value.code == "customer_not_found"
