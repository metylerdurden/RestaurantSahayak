from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk, _utcnow


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_restaurant_type_occurred", "restaurant_id", "event_type", "occurred_at"),
        Index("ix_events_unhandled", "id", postgresql_where=text("NOT handled")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    handled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
