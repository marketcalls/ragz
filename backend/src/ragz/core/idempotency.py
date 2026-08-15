"""Atomic single-claim idempotency (RAGZ-PUB-10). Signature verification
proves a delivery is authentic, not that it is FRESH -- a captured, still
valid delivery replayed within its signature's validity window (Slack/
Discord's 5-minute skew tolerance; Telegram's static secret has no window at
all) would otherwise cause duplicate LLM work + duplicate outbound messages.
`claim_once` closes that gap with a Redis `SET key val NX EX ttl`: NX makes
the write conditional on the key being absent, and Redis executes SET as a
single atomic command, so two concurrent requests carrying the same
platform-issued unique id can never both win the claim -- exactly one caller
sees `True`."""

from redis.asyncio import Redis


async def claim_once(redis: Redis, key: str, ttl_seconds: int) -> bool:
    """Atomically claims `key` for `ttl_seconds`.

    Returns True the first time this key is seen (the claim succeeded --
    caller should proceed with the work). Returns False if the key was
    already claimed by an earlier call within the TTL window (a replay, or a
    platform's own at-least-once retry -- caller should skip the work but
    still return the platform's expected success response, so the platform
    stops retrying)."""
    claimed = await redis.set(key, "1", nx=True, ex=ttl_seconds)
    return bool(claimed)


# RAGZ-PUB-10 follow-up: `claim_once`'s TTL window is the right defense when
# a signature's own validity is itself time-bounded (Slack/Discord). Telegram
# has NO signed timestamp at all -- its secret-token header is a static bearer
# credential -- so a delivery captured once stays replayable forever, and a
# 900s TTL dedup key merely delays the replay past the window instead of
# closing it. Telegram's `update_id` is monotonically increasing per bot, so
# a persistent (long-TTL, effectively "forever" for practical purposes)
# high-water mark closes the gap completely: anything at or below the
# highest `update_id` ever seen is rejected, with no expiry to outlive.
_HIGH_WATER_MARK_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current and tonumber(current) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""


async def claim_monotonic(redis: Redis, key: str, value: int, ttl_seconds: int) -> bool:
    """Atomically claims `key` as a monotonic high-water mark.

    Returns True (and stores `value`) iff `value` is strictly greater than
    whatever is currently stored under `key` (or nothing is stored yet).
    Returns False -- with NO write -- if `value` is less than or equal to
    the current mark: a replay, or a legitimately out-of-order-but-stale
    delivery (both are treated identically -- see caller for the tradeoff).

    Atomicity: the read-compare-write is a single Lua script (`EVAL`), which
    Redis executes as one indivisible command server-side -- unlike a bare
    `GET` followed by a conditional `SET`, which leaves a TOCTOU race window
    between two concurrent callers racing the same key. A small script is
    preferred here over a client-side compare-and-swap loop precisely to
    avoid that race, even though the platforms calling this (Telegram) very
    rarely redeliver concurrently -- correctness shouldn't depend on that
    being rare.

    `ttl_seconds` should be long relative to any realistic redelivery window
    (this is a persistent mark, not a short replay-dedup claim) -- callers
    pick it large enough that the mark effectively never expires in
    practice, while still bounding unbounded Redis growth for
    long-abandoned integrations."""
    # redis-py's stub types Redis.eval's return as `Awaitable[str] | str`
    # (shared with the sync client) rather than the async client's actual
    # always-awaitable return -- same mismatch as ops/health.py's `llen`
    # call, same fix.
    result = await redis.eval(  # type: ignore[misc]
        _HIGH_WATER_MARK_SCRIPT, 1, key, str(value), str(ttl_seconds)
    )
    return bool(result)
