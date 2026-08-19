"""app.core.telemetry itself: configure_telemetry()'s idempotency (safe to call from
every create_app()/script without ever replacing the test session's InMemorySpanExporter-
backed provider — see tests/conftest.py), and the start_span() helper's attribute
handling and exception recording, which every instrumented module in this codebase
(agents, tools, MemoryService, EventBus, ApprovalService, BackgroundWorkflow, the
Ollama provider) relies on for consistent behavior."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from app.core.config import Settings
from app.core.telemetry import configure_telemetry, get_tracer, start_span


def _settings(**overrides) -> Settings:
    defaults = dict(database_url="postgresql+asyncpg://x:x@localhost/x")
    defaults.update(overrides)
    return Settings(**defaults)


def test_configure_telemetry_does_not_replace_an_already_installed_provider():
    """The test session (tests/conftest.py) installs a real SDK TracerProvider before
    any app code runs specifically so spans are captured for assertions instead of
    printed/shipped — configure_telemetry() must leave that alone."""
    provider_before = trace.get_tracer_provider()
    configure_telemetry(_settings())
    configure_telemetry(_settings(otel_exporter="otlp"))
    assert trace.get_tracer_provider() is provider_before


def test_start_span_sets_given_attributes_and_skips_none_values(otel_spans):
    tracer = get_tracer(__name__)
    with start_span(tracer, "test.op", tool_name="get_inventory", restaurant_id=None, agent_name="inventory"):
        pass

    spans = otel_spans.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "test.op"
    assert span.attributes["tool_name"] == "get_inventory"
    assert span.attributes["agent_name"] == "inventory"
    assert "restaurant_id" not in span.attributes


def test_start_span_records_exception_and_sets_error_status_then_reraises(otel_spans):
    tracer = get_tracer(__name__)

    with pytest.raises(RuntimeError):
        with start_span(tracer, "test.op"):
            raise RuntimeError("boom")

    spans = otel_spans.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)


def test_start_span_is_the_current_span_so_nested_spans_become_children(otel_spans):
    tracer = get_tracer(__name__)
    with start_span(tracer, "outer"):
        with start_span(tracer, "inner"):
            pass

    spans = {s.name: s for s in otel_spans.get_finished_spans()}
    assert spans["inner"].parent.span_id == spans["outer"].context.span_id
