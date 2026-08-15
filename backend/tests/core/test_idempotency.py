"""RAGZ-PUB-10: unit coverage for the atomic replay-claim helper used by the
bot webhook routes (api/routes/bots.py). Redis's SET NX EX is a single
atomic command, so this only needs to prove first-claim-wins/second-claim-
loses and the TTL expiry -- the atomicity itself is Redis's own guarantee,
not something a single-process test can meaningfully race."""

import asyncio

from redis.asyncio import Redis

from ragz.core.idempotency import claim_once


async def test_first_claim_wins_second_claim_loses(redis_client: Redis) -> None:
    assert await claim_once(redis_client, "webhook_seen:test:1", ttl_seconds=60) is True
    assert await claim_once(redis_client, "webhook_seen:test:1", ttl_seconds=60) is False
    assert await claim_once(redis_client, "webhook_seen:test:1", ttl_seconds=60) is False


async def test_different_keys_each_claim_independently(redis_client: Redis) -> None:
    assert await claim_once(redis_client, "webhook_seen:test:a", ttl_seconds=60) is True
    assert await claim_once(redis_client, "webhook_seen:test:b", ttl_seconds=60) is True


async def test_claim_expires_after_ttl(redis_client: Redis) -> None:
    assert await claim_once(redis_client, "webhook_seen:test:ttl", ttl_seconds=1) is True
    await asyncio.sleep(1.1)
    assert await claim_once(redis_client, "webhook_seen:test:ttl", ttl_seconds=1) is True
