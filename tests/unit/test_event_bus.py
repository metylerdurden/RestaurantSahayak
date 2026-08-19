"""InProcessEventBus against a mocked EventRepository — no database. Proves the bus
contract itself: persistence-then-dispatch ordering, multiple handlers per event
type, per-handler error isolation with bounded retry, idempotent publish, and
rejecting an unknown event_type at both subscribe() and publish() time. Real
persistence and real reactive workflows (real DB, real/scripted agents) are covered
by tests/integration/test_event_bus.py and
tests/integration/test_event_driven_workflows.py."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.repositories.event_repo import EventRepository
from app.services.event_bus import InProcessEventBus
from app.tools.base import ToolError

RESTAURANT_ID = uuid.uuid4()


def _event_row(**overrides):
    from datetime import datetime, timezone

    defaults = dict(
        id=uuid.uuid4(),
        event_type="reservation.created",
        restaurant_id=RESTAURANT_ID,
        entity_id=uuid.uuid4(),
        payload={"party_size": 4},
        correlation_id=uuid.uuid4(),
        published_by="reservation_service",
        idempotency_key=None,
        created_at=datetime.now(timezone.utc),
        handled=False,
        handled_at=None,
        handler_results=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def repo():
    r = AsyncMock(spec=EventRepository)
    r.create.side_effect = lambda **kwargs: _event_row(**kwargs)
    r.save.side_effect = lambda event: event
    r.get_by_idempotency_key.return_value = None
    return r


@pytest.fixture
def bus(repo):
    return InProcessEventBus(repo)


@pytest.mark.asyncio
async def test_publish_persists_the_event_with_all_required_fields(bus, repo):
    envelope = await bus.publish(
        event_type="reservation.created",
        restaurant_id=RESTAURANT_ID,
        entity_id=uuid.uuid4(),
        payload={"party_size": 4},
        published_by="reservation_service",
    )
    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["event_type"] == "reservation.created"
    assert kwargs["restaurant_id"] == RESTAURANT_ID
    assert kwargs["payload"] == {"party_size": 4}
    assert kwargs["published_by"] == "reservation_service"
    assert isinstance(kwargs["correlation_id"], uuid.UUID)  # auto-generated when not supplied
    assert envelope.event_type == "reservation.created"


@pytest.mark.asyncio
async def test_publish_rejects_unknown_event_type(bus):
    with pytest.raises(ToolError) as exc_info:
        await bus.publish(
            event_type="not.a.real.event", restaurant_id=RESTAURANT_ID, entity_id=None,
            payload={}, published_by="x",
        )
    assert exc_info.value.code == "invalid_event_type"


def test_subscribe_rejects_unknown_event_type(bus):
    async def handler(event):
        pass

    with pytest.raises(ToolError) as exc_info:
        bus.subscribe("not.a.real.event", handler)
    assert exc_info.value.code == "invalid_event_type"


@pytest.mark.asyncio
async def test_publish_dispatches_to_a_subscribed_handler(bus):
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("reservation.created", handler, name="test_handler")
    envelope = await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={"party_size": 2}, published_by="reservation_service",
    )

    assert len(received) == 1
    assert received[0].event_id == envelope.event_id
    assert received[0].payload == {"party_size": 2}


@pytest.mark.asyncio
async def test_publish_dispatches_to_multiple_handlers_independently(bus):
    calls = []

    async def handler_a(event):
        calls.append("a")

    async def handler_b(event):
        calls.append("b")

    bus.subscribe("reservation.created", handler_a, name="a")
    bus.subscribe("reservation.created", handler_b, name="b")
    await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )

    assert sorted(calls) == ["a", "b"]


@pytest.mark.asyncio
async def test_publish_does_not_dispatch_to_handlers_of_a_different_event_type(bus):
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("inventory.low", handler)
    await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )

    assert received == []


@pytest.mark.asyncio
async def test_publish_never_raises_when_a_handler_fails(bus, repo):
    async def failing_handler(event):
        raise RuntimeError("boom")

    bus.subscribe("reservation.created", failing_handler, name="failing")
    envelope = await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )
    assert envelope is not None  # publish() completed despite the handler blowing up


@pytest.mark.asyncio
async def test_one_handler_failing_does_not_stop_another_handler_from_running(bus):
    calls = []

    async def failing_handler(event):
        raise RuntimeError("boom")

    async def working_handler(event):
        calls.append("worked")

    bus.subscribe("reservation.created", failing_handler, name="failing")
    bus.subscribe("reservation.created", working_handler, name="working")
    await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )

    assert calls == ["worked"]


@pytest.mark.asyncio
async def test_failing_handler_is_retried_up_to_the_configured_limit(repo):
    bus = InProcessEventBus(repo, max_retries_per_handler=2)
    attempts = []

    async def flaky_handler(event):
        attempts.append(1)
        raise RuntimeError("still failing")

    bus.subscribe("reservation.created", flaky_handler)
    await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )

    assert len(attempts) == 3  # 1 initial attempt + 2 retries


@pytest.mark.asyncio
async def test_handler_recovering_on_retry_counts_as_success(repo):
    bus = InProcessEventBus(repo, max_retries_per_handler=1)
    attempts = []

    async def recovers_on_second_try(event):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")

    bus.subscribe("reservation.created", recovers_on_second_try)
    await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )

    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_event_is_marked_handled_with_handler_results_after_dispatch(bus, repo):
    async def handler(event):
        return None

    bus.subscribe("reservation.created", handler, name="my_handler")
    await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x",
    )

    saved_event = repo.save.call_args.args[0]
    assert saved_event.handled is True
    assert saved_event.handled_at is not None
    assert saved_event.handler_results == {"my_handler": "success"}


@pytest.mark.asyncio
async def test_publish_with_same_idempotency_key_does_not_redispatch(repo):
    bus = InProcessEventBus(repo)
    calls = []

    async def handler(event):
        calls.append(1)

    bus.subscribe("reservation.created", handler)

    first = await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x", idempotency_key="res-123-created",
    )
    # Simulate the retried publish finding the already-persisted event.
    repo.get_by_idempotency_key.return_value = _event_row(
        id=first.event_id, event_type="reservation.created", restaurant_id=RESTAURANT_ID,
        idempotency_key="res-123-created", correlation_id=first.correlation_id,
        published_by="x", entity_id=first.entity_id, payload={},
    )
    second = await bus.publish(
        event_type="reservation.created", restaurant_id=RESTAURANT_ID, entity_id=uuid.uuid4(),
        payload={}, published_by="x", idempotency_key="res-123-created",
    )

    assert second.event_id == first.event_id
    assert len(calls) == 1  # the handler only ran once, for the first publish
    repo.create.assert_called_once()
