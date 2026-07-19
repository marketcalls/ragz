"""Pure-ASGI request-id middleware.

Deliberately NOT BaseHTTPMiddleware: that wrapper re-buffers streaming
responses and breaks SSE cancellation semantics (Task 1 depends on the raw
generator lifecycle reaching stream_reply).
"""

import uuid
from typing import Any

from structlog.contextvars import bind_contextvars, clear_contextvars

Scope = dict[str, Any]


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
            incoming.decode("latin-1")[:64] if incoming else str(uuid.uuid4())
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
