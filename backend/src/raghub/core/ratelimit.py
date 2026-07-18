import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import Request

from raghub.core.errors import RateLimitExceeded


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = [t for t in self._hits[key] if now - t < self.window_seconds]
        if len(window) >= self.limit:
            self._hits[key] = window
            raise RateLimitExceeded("rate limit exceeded, retry later")
        window.append(now)
        self._hits[key] = window


def rate_limit(
    scope: str, limit: int = 10, window_seconds: int = 60
) -> Callable[[Request], Awaitable[None]]:
    limiter = FixedWindowLimiter(limit, window_seconds)

    async def guard(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        # Keyed on the app instance too: the route decorator (and thus this
        # closure's `limiter`) is created once at module-import time, so it is
        # shared across every FastAPI app built afterwards. Production only ever
        # builds one app per process, so this has no effect there; in tests each
        # `client` fixture builds a fresh app, and including `id(request.app)` in
        # the key keeps those apps' counters isolated instead of leaking across
        # tests within the same process.
        limiter.check(f"{scope}:{id(request.app)}:{client_ip}")

    return guard
