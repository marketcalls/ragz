"""Edge-hardening ASGI middlewares (RAGZ-PUB-09, RAGZ-PUB-03, RAGZ-PUB-06).

Deliberately pure-ASGI, not Starlette's `BaseHTTPMiddleware`: that wrapper
re-buffers streaming responses and breaks SSE cancellation semantics (see
`ragz.core.middleware.RequestIDMiddleware`'s docstring -- chat streaming
depends on the raw generator lifecycle reaching `stream_reply`). Every
middleware here only ever inspects/mutates ASGI messages in-flight, never
buffers a full response body.
"""

import json
from typing import Any
from urllib.parse import urlsplit

Scope = dict[str, Any]
Message = dict[str, Any]

_PROBLEM_MEDIA_TYPE = b"application/problem+json"

# RAGZ-PUB-03: the multipart document-upload route already streams+caps a
# single file against settings.max_upload_mb (documents.py) before this
# middleware ever sees the bytes. This ceiling is a global backstop for
# every other body on the app -- JSON payloads, Slack/Discord/Telegram
# webhook deliveries, etc. -- and for a lying/absent Content-Length on any
# route (it caps actual bytes read off the wire, not just the header).
#
# It is set ABOVE max_upload_mb (with a margin for multipart framing
# overhead: boundary lines, per-part headers, the non-file form fields)
# so it can never clip a legitimate upload that already passed the
# route's own tighter check. Floor of 25 MB guards a deployment that sets
# max_upload_mb very low from ending up with an unreasonably small global
# ceiling for ordinary JSON traffic.
_BODY_CEILING_MIN_MB = 25
_BODY_CEILING_MARGIN_MB = 10


def body_size_ceiling_bytes(max_upload_mb: int) -> int:
    """Global request-body ceiling in bytes, derived from the existing
    settings.max_upload_mb (no new config field added -- config.py is out
    of scope for this change)."""
    ceiling_mb = max(max_upload_mb, _BODY_CEILING_MIN_MB) + _BODY_CEILING_MARGIN_MB
    return ceiling_mb * 1024 * 1024


def trusted_hosts_for(environment: str, public_api_base_url: str) -> list[str]:
    """RAGZ-PUB-06: Host header allowlist for TrustedHostMiddleware.

    There is no dedicated `allowed_hosts` field on Settings yet (config.py
    is owned by another change and out of scope here). Production derives
    a single trusted host from `public_api_base_url` -- the API's own
    canonical origin, which `Settings._production_fails_closed` already
    forces to https:// in production.

    TODO(RAGZ-PUB-06 follow-up): once config.py is back in scope, add a
    proper `RAGZ_ALLOWED_HOSTS` list so multi-host / CDN / multi-region
    deployments don't have to keep reusing public_api_base_url.

    dev/test/staging stay permissive ("*") so this never breaks local
    tooling, docker-compose service-name hosts, or the ASGI test client's
    "testserver" Host header.
    """
    if environment != "production":
        return ["*"]
    host = urlsplit(public_api_base_url).hostname
    return [host] if host else ["*"]


def _problem_body(status: int, title: str, detail: str) -> bytes:
    return json.dumps(
        {"type": "about:blank", "title": title, "status": status, "detail": detail}
    ).encode("utf-8")


async def _send_413(send: Any, max_bytes: int) -> None:
    detail = f"request body exceeds the {max_bytes}-byte limit"
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", _PROBLEM_MEDIA_TYPE)],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": _problem_body(413, "Payload Too Large", detail),
            "more_body": False,
        }
    )


class _BodyTooLarge(Exception):
    """Internal signal: raised inside the wrapped `receive`, caught in
    `__call__` to turn it into a 413 rather than letting it surface as an
    unhandled 500 from deep inside the downstream ASGI app."""


class BodySizeLimitMiddleware:
    """Rejects any HTTP request whose body exceeds `max_bytes`.

    Two enforcement paths:
    - Declared `Content-Length` over the ceiling -> immediate 413, before
      any body is read at all.
    - Chunked / unknown-length bodies (no Content-Length, or one that
      understates the truth) -> the wrapped `receive()` accumulates actual
      bytes seen and aborts with a 413 once the running total crosses the
      ceiling, without ever buffering the full oversized body in memory.
    """

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_len = int(declared)
            except ValueError:
                declared_len = None
            if declared_len is not None and declared_len > self.max_bytes:
                await _send_413(send, self.max_bytes)
                return

        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen
            message: Message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body") or b"")
                if seen > self.max_bytes:
                    raise _BodyTooLarge
            return message

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            # The downstream app hadn't started a response yet (the normal
            # case: body is fully read before any handler responds) -- safe
            # to answer with 413 ourselves. If a response had already
            # started (streaming handler reading incrementally), sending a
            # second http.response.start would violate the ASGI protocol,
            # so re-raise and let the server-level error handling take it.
            if response_started:
                raise
            await _send_413(send, self.max_bytes)


class SecurityHeadersMiddleware:
    """Appends a fixed set of hardening response headers (RAGZ-PUB-09) to
    every HTTP response -- including streamed SSE chat responses and the
    problem+json responses produced by the app's global exception
    handlers, since this sits inside the exception-handling layer of the
    middleware stack and only ever touches the outgoing ASGI messages."""

    def __init__(self, app: Any, *, hsts: bool) -> None:
        self.app = app
        headers: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (b"content-security-policy", b"frame-ancestors 'none'"),
            (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
        ]
        if hsts:
            # Only ever sent when settings.environment == "production" (the
            # caller gates this) -- HSTS in dev/test would get cached by a
            # browser against localhost and break plain-http local tooling.
            headers.append(
                (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
            )
        self._headers = headers

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._headers)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
