"""Structured logging (structlog) with a per-request correlation id.

The correlation id is stored in a ``contextvars.ContextVar`` so it is available to
every log call made while handling one request/agent run, without threading it through
every function signature by hand.
"""

from __future__ import annotations

import contextvars
import logging
import uuid

import structlog

from app.core.config import Settings

_correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def bind_correlation_id(correlation_id: str | None = None) -> str:
    """Set (or generate) the correlation id for the current context. Returns it."""
    cid = correlation_id or new_correlation_id()
    _correlation_id_var.set(cid)
    return cid


def get_correlation_id() -> str | None:
    return _correlation_id_var.get()


def get_correlation_id_uuid() -> uuid.UUID | None:
    """The current request's correlation id as a UUID, for callers that need to
    thread the *same* id an agent run/OTel span uses (AgentRun.correlation_id is a
    UUID column) — not just a log line. Returns None if unset, or if an external
    caller supplied an X-Correlation-Id that isn't a valid UUID (fine for log
    correlation, not for a UUID column) — callers should treat None the same as
    "no correlation id supplied" and let a fresh one be generated downstream."""
    cid = get_correlation_id()
    if cid is None:
        return None
    try:
        return uuid.UUID(cid)
    except ValueError:
        return None


def _add_correlation_id(logger, method_name, event_dict):  # noqa: ANN001, ARG001
    cid = get_correlation_id()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging once, at process startup."""
    logging.basicConfig(level=settings.log_level, format="%(message)s")

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    renderer: structlog.typing.Processor
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)
