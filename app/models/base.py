"""Declarative base + shared conventions for all ORM models (Phase 2+).

Conventions (see specs/001-core-platform-mvp/data-model.md "Schema-wide conventions"):
- uuid primary keys everywhere, generated application-side (uuid4).
- every mutable table gets created_at/updated_at via TimestampMixin.
- bounded-vocabulary fields are `text` + `CHECK`, not native Postgres ENUM types.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
