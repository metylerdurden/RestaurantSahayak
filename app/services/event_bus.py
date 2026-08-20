"""EventBus: DineOps' internal event-driven architecture core.

    <a domain service mutates something> -> EventBus.publish() -> Event persisted
    -> dispatched synchronously, in-process, to every workflow handler subscribed
    to that event_type -> each handler decides what (if anything) to do, typically
    by calling exactly one specialist agent's .handle()

        Event -> EventBus -> Workflow -> Agent -> Tools -> Services -> Database

Deliberately synchronous, in-process dispatch (no external broker, no background
worker): this codebase uses one shared AsyncSession per request/transaction
throughout (see app.core.db), and SQLAlchemy's AsyncSession is not safe for
concurrent use from more than one coroutine at a time — a fire-and-forget background
task touching the same session while the original request kept using it would be a
real correctness bug, not just a style choice. Publishing and dispatch happen in the
same session as the mutation that triggered them, so they're part of the same
transaction: if it rolls back, the event and any handler side effects never
committed either. A production deployment with per-worker sessions/connections could
move dispatch to a queue behind the same `EventBus` interface without touching any
publisher or handler; this documents the tradeoff rather than hiding it, the same
way this project documents its in-memory LangGraph checkpointer and lack of a
message broker at all.

Publishing never raises because a handler failed: each handler is isolated (one
handler's exception never stops another's or the publisher), retried up to
`max_retries_per_handler` times, and its outcome recorded on the Event row rather
than propagated — a reservation must still get created even if the reactive
inventory check that runs afterward blows up.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Awaitable, Callable

from opentelemetry.trace import Status, StatusCode

from app.core.logging import get_logger
from app.core.telemetry import get_tracer, start_span
from app.models.event import EVENT_TYPES
from app.repositories.event_repo import EventRepository
from app.schemas.event import EventEnvelope
from app.tools.base import ToolError, utcnow

EventHandler = Callable[[EventEnvelope], Awaitable[Any]]

_logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class EventBus(ABC):
    @abstractmethod
    def subscribe(self, event_type: str, handler: EventHandler, *, name: str | None = None) -> None:
        """Register a handler to run whenever `event_type` is published. Multiple
        handlers may subscribe to the same event_type; each runs independently."""

    @abstractmethod
    async def publish(
        self,
        *,
        event_type: str,
        restaurant_id: uuid.UUID,
        entity_id: uuid.UUID | None,
        payload: dict[str, Any],
        published_by: str,
        correlation_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> EventEnvelope:
        """Persist the event and dispatch it to every subscribed handler before
        returning. `idempotency_key`, when given, makes this call safe to retry: a
        second publish() with the same (restaurant_id, idempotency_key) returns the
        already-persisted event instead of creating a duplicate or re-dispatching."""


class _HandlerRegistration:
    __slots__ = ("name", "handler")

    def __init__(self, name: str, handler: EventHandler) -> None:
        self.name = name
        self.handler = handler


class InProcessEventBus(EventBus):
    def __init__(self, repo: EventRepository, *, max_retries_per_handler: int = 1) -> None:
        self.repo = repo
        self.max_retries_per_handler = max_retries_per_handler
        self._handlers: dict[str, list[_HandlerRegistration]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler, *, name: str | None = None) -> None:
        if event_type not in EVENT_TYPES:
            raise ToolError("invalid_event_type", f"Unknown event_type: {event_type!r}")
        handler_name: str = name if name is not None else getattr(handler, "__name__", "handler")
        self._handlers[event_type].append(_HandlerRegistration(handler_name, handler))

    async def publish(
        self,
        *,
        event_type: str,
        restaurant_id: uuid.UUID,
        entity_id: uuid.UUID | None,
        payload: dict[str, Any],
        published_by: str,
        correlation_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> EventEnvelope:
        # No `payload` as a span attribute — it's the domain event's own data
        # (reservation details, purchase amounts, ...), not an identifier.
        with start_span(
            _tracer, "event.publish", event_type=event_type, restaurant_id=str(restaurant_id), published_by=published_by
        ) as span:
            if event_type not in EVENT_TYPES:
                raise ToolError("invalid_event_type", f"Unknown event_type: {event_type!r}")

            if idempotency_key is not None:
                existing = await self.repo.get_by_idempotency_key(restaurant_id, idempotency_key)
                if existing is not None:
                    _logger.info(
                        "event_bus.duplicate_publish_skipped",
                        event_type=event_type,
                        idempotency_key=idempotency_key,
                        event_id=str(existing.id),
                    )
                    span.set_attribute("event_id", str(existing.id))
                    span.set_attribute("deduplicated", True)
                    span.set_attribute("success", True)
                    return self._to_envelope(existing)

            event = await self.repo.create(
                event_type=event_type,
                restaurant_id=restaurant_id,
                entity_id=entity_id,
                payload=payload,
                correlation_id=correlation_id or uuid.uuid4(),
                published_by=published_by,
                idempotency_key=idempotency_key,
                handled=False,
            )
            envelope = self._to_envelope(event)
            span.set_attribute("event_id", str(event.id))
            span.set_attribute("correlation_id", str(event.correlation_id))
            log = _logger.bind(event_id=str(event.id), event_type=event_type, restaurant_id=str(restaurant_id))
            log.info("event_bus.published")

            handler_results: dict[str, str] = {}
            for registration in self._handlers.get(event_type, []):
                handler_results[registration.name] = await self._dispatch_one(registration, envelope, log)

            event.handled = True
            event.handled_at = utcnow()
            event.handler_results = handler_results or None
            await self.repo.save(event)

            span.set_attribute("handler_count", len(handler_results))
            span.set_attribute("success", all(v == "success" for v in handler_results.values()))
            return envelope

    async def _dispatch_one(self, registration: _HandlerRegistration, envelope: EventEnvelope, log: Any) -> str:
        with start_span(
            _tracer,
            "event.handle",
            event_type=envelope.event_type,
            handler=registration.name,
            restaurant_id=str(envelope.restaurant_id),
            correlation_id=str(envelope.correlation_id),
        ) as span:
            attempt = 0
            last_error: Exception | None = None
            while attempt <= self.max_retries_per_handler:
                try:
                    await registration.handler(envelope)
                    if attempt > 0:
                        log.info("event_bus.handler_recovered", handler=registration.name, attempt=attempt)
                    span.set_attribute("success", True)
                    span.set_attribute("attempts", attempt + 1)
                    return "success"
                except Exception as exc:  # one handler's failure must never break another's or the publisher
                    last_error = exc
                    log.warning("event_bus.handler_failed", handler=registration.name, attempt=attempt, error=str(exc))
                    attempt += 1

            # The loop above only exits here after at least one failing attempt
            # (every path that doesn't fail returns "success" early) — last_error
            # is always set by this point.
            assert last_error is not None
            span.set_attribute("success", False)
            span.set_attribute("attempts", attempt)
            span.record_exception(last_error)
            span.set_status(Status(StatusCode.ERROR, str(last_error)))
            return f"failed_after_retries: {last_error}"

    def _to_envelope(self, event: Any) -> EventEnvelope:
        return EventEnvelope(
            event_id=event.id,
            event_type=event.event_type,
            restaurant_id=event.restaurant_id,
            entity_id=event.entity_id,
            payload=event.payload,
            correlation_id=event.correlation_id,
            published_by=event.published_by,
            created_at=event.created_at,
        )
