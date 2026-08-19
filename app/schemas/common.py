"""Shared Pydantic field types used across multiple tool-input schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def _assume_utc(value: datetime) -> datetime:
    """A live LLM producing tool-call arguments routinely omits a UTC offset (e.g.
    "2026-08-18T20:00" instead of "...+00:00") even when told the current time is
    UTC. Treat a naive datetime as UTC rather than raising deep inside comparison
    logic (`naive <= aware` is a TypeError, not a clean ToolError)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


UTCDatetime = Annotated[datetime, AfterValidator(_assume_utc)]
