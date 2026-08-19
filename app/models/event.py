"""Event: the durable record behind DineOps' internal event-driven architecture.

    <mutation happens> -> EventBus.publish() -> Event row persisted -> dispatched
    in-process to whatever workflow handlers are subscribed to that event_type

Not a message-queue row waiting to be pulled by an external worker — this is an
in-process bus (see app.services.event_bus): publishing a domain event and
dispatching it to handlers happen synchronously, in the same request/transaction,
before publish() returns. This table exists so every event is still durably logged
(created_at, whether it was handled, and what each handler did with it) even though
dispatch itself isn't queued.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow

EVENT_TYPES = (
    "reservation.created",
    "reservation.modified",
    "reservation.cancelled",
    "inventory.updated",
    "inventory.low",
    "purchase.requested",
    "purchase.approved",
    "purchase.rejected",
    "customer.updated",
    "staffing.shortage_detected",
    "approval.created",
    "approval.completed",
)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'reservation.created','reservation.modified','reservation.cancelled',"
            "'inventory.updated','inventory.low',"
            "'purchase.requested','purchase.approved','purchase.rejected',"
            "'customer.updated','staffing.shortage_detected',"
            "'approval.created','approval.completed')",
            name="ck_events_event_type_valid",
        ),
        Index("ix_events_restaurant_type_created", "restaurant_id", "event_type", "created_at"),
        Index("ix_events_correlation_id", "correlation_id"),
        Index("ix_events_entity_id", "entity_id"),
        Index("ix_events_unhandled", "id", postgresql_where=text("NOT handled")),
        # NULLs don't collide in a unique index, so idempotency_key is only unique
        # among events that actually supplied one — most publishes don't.
        Index(
            "uq_events_idempotency_key",
            "restaurant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()  # == event_id
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    # The row this event is about (a reservation id, inventory item id, purchase
    # request id, customer id, approval id, ...) — nullable because not every
    # conceivable event is about exactly one row, though every event type defined
    # today is.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    published_by: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    handled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # {handler_name: "success" | "failed_after_retries: <error>"} — one entry per
    # subscribed handler this event was dispatched to.
    handler_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
