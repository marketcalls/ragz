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
            route = scope.get("route")
            template = getattr(route, "path", None)
            if not isinstance(template, str) or not template:
                # 404s and anything else that never matched collapse to one
                # series instead of leaking arbitrary URLs into label values.
                template = "unmatched"
            method = str(scope.get("method", "GET"))
            http_requests_total.labels(
                method=method, route=template, status=str(status)
            ).inc()
            http_request_duration_seconds.labels(method=method, route=template).observe(
                perf_counter() - started
            )
