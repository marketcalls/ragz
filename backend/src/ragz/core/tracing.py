"""OpenTelemetry tracing (Phase 3 item 1, tracing half).

Off unless `RAGZ_OTEL_ENDPOINT` is set. When it is unset, `configure_tracing`
does nothing at all and every call site below resolves to OpenTelemetry's
built-in no-op tracer -- spans are still "started", they simply cost a couple of
attribute lookups and go nowhere. That is what makes it safe to instrument hot
paths unconditionally instead of guarding each one with `if tracing_enabled`.

Deliberately hand-rolled rather than opentelemetry-instrumentation-fastapi:

- The auto-instrumentation packages carry their own ASGI middleware, and this
  app already refuses BaseHTTPMiddleware-style wrapping because it re-buffers
  streaming responses and breaks SSE cancellation (see core/middleware.py).
  Owning the middleware keeps that guarantee ours to enforce.
- They pin narrowly against FastAPI/Starlette versions and are a recurring
  source of dependency conflicts on upgrade, for behaviour that is ~40 lines
  here.

Span names use the ROUTE TEMPLATE, never the request path, for the same reason
metrics labels do: paths carry UUIDs, and a span name per document id makes
trace search useless and the backend's cardinality budget unhappy.
"""

from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

_INSTRUMENTATION_NAME = "ragz"

#: Set once by configure_tracing so a second call (tests build many app
#: instances) does not stack exporters onto the global provider.
_configured = False


def configure_tracing(
    *, endpoint: str, service_name: str = "ragz-api", _exporter: Any = None
) -> bool:
    """Install a global TracerProvider exporting over OTLP/HTTP.

    Returns True if tracing was installed, False if it was already configured.
    `_exporter` is a seam for tests to substitute an in-memory exporter -- so
    the propagation behaviour can be asserted without standing up a collector,
    which is the part of tracing that usually goes untested.
    """
    global _configured
    if _configured:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = _exporter if _exporter is not None else OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True
    return True


def get_tracer() -> trace.Tracer:
    """The app's tracer. Without configure_tracing this is the API's no-op
    implementation, which is exactly what we want when tracing is off."""
    return trace.get_tracer(_INSTRUMENTATION_NAME)


def inject_context(carrier: dict[str, str]) -> dict[str, str]:
    """Write the CURRENT span context into `carrier` as W3C traceparent.

    Used at every process boundary this app has -- publishing to the outbox and
    handing work to Celery -- so a trace that starts in an HTTP request keeps
    going in the worker instead of restarting as an orphan. That end-to-end
    stitching is the thing the audit asked for; a per-process trace would be
    much less useful for the async ingestion path, which is precisely where
    latency hides.
    """
    propagate.inject(carrier)
    return carrier


def extract_context(carrier: dict[str, str]) -> Any:
    """Rebuild a parent context from a carrier produced by inject_context."""
    return propagate.extract(carrier)


def record_exception(span: Span, exc: BaseException) -> None:
    """Mark a span failed. Kept in one place so the status/exception pair is
    always set together -- a span with an exception event but an unset status
    is invisible to a backend's error filters, which is the failure mode that
    makes people distrust their traces."""
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))


__all__ = [
    "SpanKind",
    "Status",
    "StatusCode",
    "configure_tracing",
    "extract_context",
    "get_tracer",
    "inject_context",
    "record_exception",
]
