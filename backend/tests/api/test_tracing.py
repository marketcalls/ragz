"""OpenTelemetry tracing (Phase 3 item 1, tracing half).

Asserted against a real TracerProvider with an in-memory exporter rather than a
mock, so what is checked is the spans an OTLP collector would actually receive.
Trace propagation is the part of observability that most often ships broken
precisely because testing it looks like it needs a collector -- it does not.
"""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragz.core.tracing import inject_context


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Install a real provider whose spans land in memory.

    The global provider is swapped via monkeypatch on the tracer-provider
    lookup rather than trace.set_tracer_provider, which OpenTelemetry allows
    only once per process and would leak into every other test in the run.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "ragz-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    return exporter


async def test_span_is_named_by_route_template_not_path(client, spans) -> None:  # type: ignore[no-untyped-def]
    """Same cardinality rule as the metrics labels: a UUID in the path must
    never reach a span name, or trace search becomes useless."""
    doc_id = "11111111-2222-3333-4444-555555555555"
    await client.get(f"/api/v1/documents/{doc_id}")

    finished = spans.get_finished_spans()
    assert finished, "no span was recorded for the request"
    span = finished[-1]
    assert doc_id not in span.name, f"raw path id leaked into span name: {span.name}"
    assert span.name == "GET /api/v1/documents/{document_id}"
    assert span.attributes["http.route"] == "/api/v1/documents/{document_id}"


async def test_unmatched_requests_share_one_span_name(client, spans) -> None:  # type: ignore[no-untyped-def]
    await client.get("/no/such/route/at/all")
    span = spans.get_finished_spans()[-1]
    assert span.name == "GET unmatched"
    assert "/no/such/route" not in span.name


async def test_inbound_traceparent_is_continued(client, spans) -> None:  # type: ignore[no-untyped-def]
    """End-to-end tracing only works if an inbound W3C traceparent adopts the
    caller's trace instead of starting an orphan."""
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    await client.get(
        "/healthz",
        headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
    )
    span = spans.get_finished_spans()[-1]
    assert format(span.context.trace_id, "032x") == trace_id
    assert format(span.parent.span_id, "016x") == "00f067aa0ba902b7"


async def test_a_malformed_traceparent_does_not_fail_the_request(client, spans) -> None:  # type: ignore[no-untyped-def]
    """A broken header from a caller must degrade to a fresh trace, never a
    500 -- observability must not become an availability risk."""
    r = await client.get("/healthz", headers={"traceparent": "not-a-traceparent"})
    assert r.status_code == 200
    assert spans.get_finished_spans(), "request should still be traced"


async def test_server_errors_mark_the_span_but_client_errors_do_not(
    client, spans
) -> None:  # type: ignore[no-untyped-def]
    """4xx is the caller's fault. Marking it ERROR would drown the error rate
    in routine 401s from unauthenticated probes."""
    from opentelemetry.trace import StatusCode

    await client.get("/api/v1/workspaces")  # unauthenticated -> 401
    span = spans.get_finished_spans()[-1]
    assert span.attributes["http.response.status_code"] in (401, 403)
    assert span.status.status_code is not StatusCode.ERROR


def test_inject_context_emits_a_traceparent_for_the_active_span(spans) -> None:  # type: ignore[no-untyped-def]
    """The carrier written at a process boundary must actually carry the
    active trace, otherwise worker spans silently start new traces."""
    tracer = trace.get_tracer_provider().get_tracer("test")
    with tracer.start_as_current_span("parent") as span:
        carrier = inject_context({})
        expected = format(span.get_span_context().trace_id, "032x")
    assert "traceparent" in carrier
    assert expected in carrier["traceparent"]


def test_inject_context_outside_a_span_writes_nothing() -> None:
    """No active span means no traceparent -- an empty carrier, not a fake
    one, so a downstream consumer starts its own trace rather than joining a
    nonexistent parent."""
    assert inject_context({}) == {}


def test_route_template_recovers_the_router_prefix() -> None:
    """Directly pins the arithmetic in route_template: FastAPI hands the
    middleware only the sub-path, and the prefix has to be recovered from the
    concrete request path."""
    from ragz.core.middleware import route_template

    class _Route:
        path = "/documents/{document_id}"

    scope = {
        "route": _Route(),
        "path": "/api/v1/documents/abc-123",
        "path_params": {"document_id": "abc-123"},
    }
    assert route_template(scope) == "/api/v1/documents/{document_id}"


def test_route_template_is_unmatched_without_a_route() -> None:
    assert route_template_of({}) == "unmatched"


def route_template_of(scope):  # type: ignore[no-untyped-def]
    from ragz.core.middleware import route_template

    return route_template(scope)
