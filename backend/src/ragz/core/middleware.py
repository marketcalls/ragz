"""Pure-ASGI request-id middleware.

Deliberately NOT BaseHTTPMiddleware: that wrapper re-buffers streaming
responses and breaks SSE cancellation semantics (Task 1 depends on the raw
generator lifecycle reaching stream_reply).
"""

import re
import uuid
from typing import Any

from structlog.contextvars import bind_contextvars, clear_contextvars

Scope = dict[str, Any]

# Named constraint: request-id must never carry control/high bytes into the
# echoed response header (h11 can raise on invalid header bytes -> self-inflicted
# 500) or into contextvars. Only this charset survives sanitization.
_ALLOWED_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_request_id(raw: str) -> str:
    """Drop every byte outside the allowlist, then cap at 64 chars. If nothing
    survives, mint a fresh uuid4 rather than echo an empty/invalid value."""
    cleaned = _ALLOWED_CHARS.sub("", raw)[:64]
    return cleaned if cleaned else str(uuid.uuid4())


class RequestIDMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next(
            (v for k, v in scope.get("headers", []) if k == b"x-request-id"), None
        )
        request_id = (
            _sanitize_request_id(incoming.decode("latin-1"))
            if incoming
            else str(uuid.uuid4())
        )
        clear_contextvars()
        bind_contextvars(request_id=request_id)

        async def send_with_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)



def route_template(scope: Scope) -> str:
    """The matched route's FULL template, prefix included, for use as a metric
    label and a span name.

    `scope["route"].path` alone is not it. FastAPI nests included routers
    instead of flattening them, so the matched route object carries only its
    own sub-path -- "/documents/{document_id}", not
    "/api/v1/documents/{document_id}". Labelling by that would merge every
    router that happens to share a sub-path into one series, which is a
    silently wrong graph rather than a loud failure.

    `scope["root_path"]` does not help either (it stays "" for included
    routers), so the prefix is recovered arithmetically: substitute the actual
    path params back into the sub-template to rebuild the concrete suffix that
    was matched, then take whatever precedes it in the real path. Exact rather
    than a heuristic -- no guessing which segment was a parameter.

    Returns "unmatched" when nothing matched, so 404s for arbitrary URLs
    collapse to a single series instead of letting a caller mint one per URL.
    """
    route = scope.get("route")
    sub = getattr(route, "path", None)
    if not isinstance(sub, str) or not sub:
        return "unmatched"

    full = scope.get("path")
    if not isinstance(full, str) or not full:
        return sub

    concrete = sub
    for name, value in (scope.get("path_params") or {}).items():
        concrete = concrete.replace("{" + name + "}", str(value))
    if concrete and full.endswith(concrete):
        prefix = full[: len(full) - len(concrete)]
        return f"{prefix}{sub}"
    # Path params that do not round-trip (a converter that reformats its
    # value) leave the suffix unmatched. The sub-path is still correct and
    # still low-cardinality, so degrade to it rather than to the raw path.
    return sub


class MetricsMiddleware:
    """Records request count and latency per ROUTE TEMPLATE (Phase 3 item 1).

    Pure ASGI for the same reason RequestIDMiddleware is: BaseHTTPMiddleware
    re-buffers streaming responses, which would break SSE cancellation. Timing
    around `await self.app(...)` also means a streamed chat turn is measured
    until the stream actually finishes, not until its first byte -- for this
    application that is the number worth having.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from time import perf_counter

        from ragz.core.metrics import http_request_duration_seconds, http_requests_total

        started = perf_counter()
        # Defaults to 500: if the app raises before sending a response start,
        # the request DID fail and should be counted as such rather than
        # silently dropped from the error rate.
        status = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # The matched FastAPI route, set during routing -- so this reads it
            # AFTER the call, not before. `.path` is the template
            # ("/api/v1/documents/{document_id}"), which is the whole point:
            # labelling by scope["path"] would mint a series per document id.
            # Full template including the router prefix, and "unmatched" for
            # anything that never routed -- see route_template.
            template = route_template(scope)
            method = str(scope.get("method", "GET"))
            http_requests_total.labels(
                method=method, route=template, status=str(status)
            ).inc()
            http_request_duration_seconds.labels(method=method, route=template).observe(
                perf_counter() - started
            )


class TracingMiddleware:
    """Starts a SERVER span per HTTP request, continuing an inbound trace
    (Phase 3 item 1). Pure ASGI, same reasoning as the two above.

    The span is renamed at the END, not the start. A span name must be the
    route TEMPLATE for the same reason a metric label must be -- paths carry
    UUIDs, and a span name per document id makes trace search useless -- but
    the matched route is only known after routing has run. So the span opens
    with the method alone and is renamed once the template is available.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from ragz.core.tracing import (
            SpanKind,
            Status,
            StatusCode,
            extract_context,
            get_tracer,
            record_exception,
        )

        # Continue the caller's trace when they sent one. Header values arrive
        # as raw bytes; latin-1 is the HTTP header encoding, and a malformed
        # traceparent simply fails to parse into a context rather than raising.
        carrier = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        parent = extract_context(carrier)
        method = str(scope.get("method", "GET"))

        status = 500
        tracer = get_tracer()
        with tracer.start_as_current_span(
            method, context=parent, kind=SpanKind.SERVER
        ) as span:

            async def send_wrapper(message: dict[str, Any]) -> None:
                nonlocal status
                if message.get("type") == "http.response.start":
                    status = int(message["status"])
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                record_exception(span, exc)
                raise
            finally:
                template = route_template(scope)
                span.update_name(f"{method} {template}")
                span.set_attribute("http.request.method", method)
                span.set_attribute("http.route", template)
                span.set_attribute("http.response.status_code", status)
                # 5xx is the server's fault and belongs in error views; 4xx is
                # the caller's and would otherwise drown the error rate in
                # routine 401s from unauthenticated probes.
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
