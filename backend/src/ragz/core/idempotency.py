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
