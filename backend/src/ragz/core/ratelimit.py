from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.asyncio import Redis

from ragz.core.errors import RateLimitExceeded


async def check_rate_limit(redis: Redis, key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window limiter: INCR the key, arm EXPIRE on the first hit in a window.

    Shared across processes/workers via Redis (replaces the Plan A in-process
    limiter behind the same `rate_limit()` public interface). INCR and EXPIRE run
    in one pipeline (a single round trip, not a transaction) so a TTL is always
    (re)armed alongside the increment — self-healing against a key that was
    INCR'd but never got its EXPIRE (e.g. a previous call crashing between the
    two calls, which used to leave the key without a TTL forever).
    """
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_seconds, nx=True)  # nx: never reset the window on a repeat hit
    count, _ = await pipe.execute()
    if count > limit:
        raise RateLimitExceeded("rate limit exceeded, retry later")


def rate_limit(
    scope: str, limit: int = 10, window_seconds: int = 60
) -> Callable[[Request], Awaitable[None]]:
    async def guard(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        redis: Redis = request.app.state.redis
        await check_rate_limit(redis, f"rl:{scope}:{client_ip}", limit, window_seconds)

    return guard
