from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.asyncio import Redis

from ragz.core.client_ip import client_ip
from ragz.core.config import get_settings
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


async def peek_rate_limit(redis: Redis, key: str, limit: int) -> None:
    """Raise RateLimitExceeded if `key` is already AT/OVER `limit`, WITHOUT
    incrementing. Pairs with `record_failure` to build a failure-only throttle
    (RAGZ-PUB-06): a limiter that increments on every attempt would let an
    attacker lock a victim's account just by flooding it, and would count a
    legitimate user's successful logins toward the cap. Here the counter only
    advances on actual failures (`record_failure`), and this read-only check
    gates the next attempt."""
    raw = await redis.get(key)
    if raw is not None and int(raw) >= limit:
        raise RateLimitExceeded("rate limit exceeded, retry later")


async def record_failure(redis: Redis, key: str, window_seconds: int) -> None:
    """INCR a failure counter and arm its window TTL (nx, self-healing) in one
    pipeline. Same fixed-window mechanics as check_rate_limit, but split from
    the check so callers increment ONLY on a failed outcome."""
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_seconds, nx=True)
    await pipe.execute()


async def peek_daily_cap(redis: Redis, key: str, limit: int) -> bool:
    """Read-only: True iff usage recorded under `key` is still BELOW `limit`
    (another use may proceed). `limit <= 0` disables the cap entirely (always
    True -- unlimited).

    Split from `record_daily_usage` the same way `peek_rate_limit` is split
    from `record_failure` above: callers check BEFORE doing the gated work
    and increment ONLY once the work was actually carried out, so a call that
    fails downstream (e.g. the gated provider errors) never burns the
    caller's quota (RAGZ-PUB-08 item 4 follow-up: persistent per-user/day web
    search cap)."""
    if limit <= 0:
        return True
    raw = await redis.get(key)
    return raw is None or int(raw) < limit


async def record_daily_usage(redis: Redis, key: str, ttl_seconds: int) -> None:
    """INCR `key` and arm its TTL (nx, self-healing) in one pipeline -- same
    fixed-window mechanics as `record_failure`, generalized to any
    persistent per-period usage counter (not just failures). Pairs with
    `peek_daily_cap`."""
    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl_seconds, nx=True)
    await pipe.execute()


def rate_limit(
    scope: str, limit: int = 10, window_seconds: int = 60
) -> Callable[[Request], Awaitable[None]]:
    """Per-scope, per-real-client-IP fixed-window limiter (sec RAGZ-PUB-06).

    Keys on `client_ip()` (`core/client_ip.py`), not the raw TCP peer:
    behind a reverse proxy every caller would otherwise share the proxy's
    one peer address, making this limiter useless (one caller's traffic
    throttles every other tenant's). `client_ip()` only trusts
    `X-Forwarded-For` from a configured trusted proxy -- with
    `Settings.trusted_proxies` empty (the default), this is byte-for-byte
    the previous `request.client.host`-keyed behavior.

    `get_settings()` is called directly (not via FastAPI `Depends`) so this
    guard behaves identically whether it runs as a declared route
    dependency (the common case) or is invoked directly as
    `await rate_limit(...)( request)` (oidc.py's callback route, which needs
    the guard on its own try/except path rather than FastAPI's dependency
    error path) -- a `Depends(...)` default wouldn't resolve in the latter
    call shape.
    """

    async def guard(request: Request) -> None:
        ip = client_ip(request, get_settings())
        redis: Redis = request.app.state.redis
        await check_rate_limit(redis, f"rl:{scope}:{ip}", limit, window_seconds)

    return guard
