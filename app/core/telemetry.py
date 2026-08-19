"""OpenTelemetry wiring: distributed tracing for every agent operation (Step 18).

    HTTP request -> orchestrator.run -> reservation_agent.run -> tool.call -> llm.call
                                      -> inventory_agent.run -> memory.search -> tool.call
                 -> event.publish -> event.handle -> <agent>.run -> tool.call -> llm.call
                 -> workflow.run (background workflows) -> orchestrator.run / <agent>.run
                 -> approval.create / approval.approve / approval.reject / approval.expire

One process-wide TracerProvider, configured once at startup by `configure_telemetry()`
— idempotent, so it is safe to call from every `create_app()` (including the many
FastAPI apps built across the test suite): if a real SDK TracerProvider is already
installed (e.g. tests/conftest.py installs one backed by an InMemorySpanExporter before
any app code runs), this is a no-op. The exporter for a "real" run is chosen by
`Settings.otel_exporter`:

- "console" (default): spans print to stdout as JSON — zero extra infrastructure,
  works the moment `uvicorn` starts.
- "otlp": spans are batched and sent to an OTLP/HTTP collector (e.g. Jaeger, see
  `docker/docker-compose.observability.yml`) at `Settings.otel_exporter_otlp_endpoint`.
- "none": spans are created (so every instrumented code path behaves identically
  regardless of environment) but never exported anywhere.

Every span created through `start_span()` below is deliberately attribute-only: agent
names, model names, tool names, restaurant/correlation ids, statuses, counts, and
timings. Raw task/instruction text, tool arguments/results, and memory content are
NEVER attached to a span — that's what the existing structured logs (app.core.logging)
already carry for operational debugging; traces exist to show shape and timing, not
customer content.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


def configure_telemetry(settings: Settings) -> None:
    """Install the process-wide TracerProvider, once. Safe to call repeatedly (every
    create_app() does — this codebase builds a fresh FastAPI app, and therefore runs
    this function, once per test) — if a real SDK provider is already installed, this
    returns immediately rather than raising or replacing it (OpenTelemetry only allows
    `set_tracer_provider` to succeed once per process anyway).

    Deliberately does NOT tie the provider's shutdown to any single `create_app()`
    lifespan — a `TracerProvider.shutdown()` is terminal (its exporter stops
    accepting spans for the rest of the process), so if this ran once per app
    instance the *second* FastAPI app built in the same process (routine in this
    test suite, and not impossible for a script that builds more than one) would
    silently lose all tracing. `TracerProvider` already flushes/shuts down at real
    process exit on its own (`shutdown_on_exit=True`, the SDK default) — that is
    tied to process lifetime, not to any particular caller's lifecycle, which is
    exactly what's needed here."""
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    if not settings.otel_enabled:
        return

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)

    if settings.otel_exporter == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif settings.otel_exporter == "otlp":
        endpoint = f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    # "none": no span processor registered at all — spans are still created (so
    # instrumented code never branches on environment) but are simply dropped.

    trace.set_tracer_provider(provider)
    _logger.info("telemetry.configured", exporter=settings.otel_exporter, service_name=settings.otel_service_name)


def instrument_fastapi(app: Any) -> None:
    """HTTP requests (requirement 1) — every inbound request becomes a root span,
    with the correlation-id middleware (app.main) tagging it with `correlation_id`."""
    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine: Any) -> None:
    """Database operations, where practical (requirement 12): auto-instruments every
    SQL statement the app's engine executes as a span, without touching any
    repository class individually. Guarded because `engine` is a process-wide
    singleton (app.core.db) that a repeated app startup (e.g. once per test) would
    otherwise try to instrument more than once."""
    instrumentor = SQLAlchemyInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(engine=engine.sync_engine)


def shutdown_telemetry() -> None:
    """Flush any buffered spans (relevant for the "otlp" exporter's BatchSpanProcessor)
    right now, for callers that know they are the process's only/final use of the
    provider — a short-lived script (scripts/run_workflow.py), for instance. Do NOT
    call this from `create_app()`'s FastAPI lifespan: the provider is process-wide,
    `create_app()` can run more than once per process (every test does), and this
    call is terminal — see `configure_telemetry`'s own `atexit` registration, which
    is what the long-running server process relies on instead."""
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()


def get_tracer(name: str) -> Tracer:
    return trace.get_tracer(name)


@contextmanager
def start_span(tracer: Tracer, name: str, **attributes: Any) -> Iterator[Span]:
    """Start `name` as the current span, tag it with the given attributes (None
    values skipped so an absent id/field just doesn't appear rather than showing up
    as the literal string "None"), and record any exception that escapes the `with`
    block as a span error before re-raising it unchanged."""
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
