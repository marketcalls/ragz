"""Inbound chat-platform webhooks (design doc §Components item 6): verify ->
handshake/parse -> relay -> outbound. No FastAPI auth dependency here -- the
platform itself is the caller; signature verification IS the auth, and it
happens BEFORE any workspace/LLM work (iron rule). Mounted /external/bots.
Telegram (Task 5), Slack + Discord (Task 6, incl. their handshakes) all
follow the same verify-first shape."""

import json
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.bots_relay import answer_for_integration
from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthenticationError, BadRequestError, PayloadTooLarge, UpstreamError
from ragz.core.idempotency import claim_monotonic, claim_once
from ragz.core.ratelimit import check_rate_limit, rate_limit
from ragz.modules.bots import platforms
from ragz.modules.bots import service as bots_service
from ragz.modules.bots.verify import verify_discord, verify_slack, verify_telegram

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

router = APIRouter(tags=["bots"])

# Per-integration inbound limit (iron rule 4: "rate-limit inbound"). Generous
# enough for real chat traffic, tight enough to bound a compromised/probing
# webhook_id's request volume. Keyed on the resolved integration (not the
# caller's IP -- the platform's own servers are the caller, and their IPs
# are shared across every tenant's webhook traffic), so one noisy/compromised
# integration can never exhaust another integration's budget.
_INBOUND_LIMIT = 30
_INBOUND_WINDOW_SECONDS = 60

# IP-keyed limit that runs BEFORE resolve_by_webhook (mirrors auth.py's
# login/refresh dependency-based rate_limit(), the established pattern for
# gating an unauthenticated route by caller IP before any DB lookup). The
# per-integration limiter above only starts counting once a webhook_id has
# already resolved to a real row, so on its own an unauthenticated caller
# could probe an unbounded number of random webhook_ids for free (each one
# a wasted 404 DB round trip) before ever being throttled. Declared as a
# route `dependencies=[]` entry so FastAPI resolves it before the handler
# body -- and therefore before resolve_by_webhook -- runs; shared across
# telegram/slack/discord so all three carry the same probing bound. The
# limit is generous relative to the per-integration one because a single IP
# can legitimately be the source for many tenants' webhook traffic (e.g. a
# platform's outbound-webhook IP range is shared across all its customers).
_bot_webhook_ip_limit = Depends(rate_limit("bot_webhook_probe", limit=120, window_seconds=60))

# RAGZ-PUB-03: the global BodySizeLimitMiddleware (security_middleware.py)
# caps every request body on the app at ~35MB+ -- a backstop sized for
# document uploads, not chat-platform webhook events. Telegram/Slack/
# Discord inbound payloads are small JSON (a message + metadata); 64KB is
# generous headroom over any real delivery while keeping a probing/
# compromised webhook_id from forcing this route to buffer anywhere close
# to the global ceiling. One constant, enforced identically for all three
# platforms via `_read_bounded_body` below.
_WEBHOOK_BODY_MAX_BYTES = 64 * 1024

# RAGZ-PUB-10: TTL for the replay-claim keys below. A signature-verified
# delivery is only ever "fresh" (Slack/Discord's own skew tolerance) for
# _SLACK_MAX_SKEW_SECONDS/_DISCORD_MAX_SKEW_SECONDS = 300s each (verify.py);
# this window is deliberately 3x that so a claim outlives the longest window
# a still-validly-signed replay could arrive in, plus headroom for ordinary
# platform retry storms. Slack/Discord ONLY -- see _TELEGRAM_HWM_TTL_SECONDS
# below for why Telegram uses a different mechanism entirely.
_WEBHOOK_IDEMPOTENCY_TTL_SECONDS = 900

# RAGZ-PUB-10 follow-up: Telegram's secret-token header (verify_telegram) has
# NO signed timestamp bound into it at all -- unlike Slack/Discord, a
# captured valid delivery stays replayable for as long as the secret is
# unrotated, so the short _WEBHOOK_IDEMPOTENCY_TTL_SECONDS claim above only
# delayed a Telegram replay past 900s, it never closed the gap. Telegram's
# `update_id` is monotonically increasing per bot, so telegram_webhook uses
# `claim_monotonic` (core/idempotency.py) instead: a persistent high-water
# mark that rejects any update_id at or below the highest ever seen, with no
# short expiry for a replay to outlive. 30 days is "effectively forever" for
# an active integration while still bounding unbounded Redis growth for one
# that's been abandoned/disabled.
_TELEGRAM_HWM_TTL_SECONDS = 60 * 60 * 24 * 30


async def _rate_limit_bot_webhook(request: Request, integration_id: UUID) -> None:
    await check_rate_limit(
        request.app.state.redis, f"rl:bot_webhook:integration:{integration_id}",
        _INBOUND_LIMIT, _INBOUND_WINDOW_SECONDS,
    )


async def _claim_webhook_delivery(request: Request, key: str) -> bool:
    """Wraps `claim_once` with the shared TTL -- returns True if this is the
    first time `key` has been seen (caller should do the relay/LLM/outbound
    work), False if it's a replay/retry (caller must skip that work but
    still return the platform's expected success response)."""
    return await claim_once(request.app.state.redis, key, _WEBHOOK_IDEMPOTENCY_TTL_SECONDS)


async def _read_bounded_body(request: Request) -> bytes:
    """Reads the request body capped at `_WEBHOOK_BODY_MAX_BYTES`, raising
    PayloadTooLarge (413) before any signature-verification/parse/relay
    work runs. Two enforcement paths: a declared Content-Length over the cap
    is rejected before reading any bytes; then the body is streamed and the
    running total aborted the instant it exceeds the cap -- so a chunked /
    absent / understated Content-Length can never force this route to buffer
    past 64KB (the earlier `request.body()` version fell back to the global
    ~25-45MB middleware ceiling for the no-Content-Length case; RAGZ-PUB-03
    review). Mirrors BodySizeLimitMiddleware's incremental-accumulate pattern."""
    if content_length := request.headers.get("content-length"):
        try:
            if int(content_length) > _WEBHOOK_BODY_MAX_BYTES:
                raise PayloadTooLarge(
                    f"webhook body exceeds {_WEBHOOK_BODY_MAX_BYTES} byte limit"
                )
        except ValueError:
            pass  # invalid Content-Length -- the streaming cap below still bounds it
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _WEBHOOK_BODY_MAX_BYTES:
            raise PayloadTooLarge(f"webhook body exceeds {_WEBHOOK_BODY_MAX_BYTES} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_json(raw_body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise BadRequestError("malformed JSON body") from exc
    if not isinstance(payload, dict):
        raise BadRequestError("expected a JSON object body")
    return payload


@router.post("/telegram/{webhook_id}", dependencies=[_bot_webhook_ip_limit])
async def telegram_webhook(
    webhook_id: UUID, request: Request, session: SessionDep, settings: SettingsDep,
) -> dict[str, bool]:
    # resolve_by_webhook 404s on unknown/wrong-platform/disabled ids alike
    # (service.py), so probing random UUIDs learns nothing about which are
    # real -- this MUST run before rate limiting/verification key on the
    # integration, since there is no integration to key on otherwise.
    integration = await bots_service.resolve_by_webhook(
        session, platform="telegram", webhook_id=webhook_id
    )
    await _rate_limit_bot_webhook(request, integration.id)
    # Raw bytes read ONCE and reused for both verify and json-parse below --
    # verifying over a re-serialized parsed body would let a byte-for-byte
    # different (but semantically equal) payload slip past a signature
    # computed over the ORIGINAL bytes Telegram sent. Bounded to
    # _WEBHOOK_BODY_MAX_BYTES (413 if exceeded) before any of that runs.
    raw_body = await _read_bounded_body(request)
    secret = await bots_service.get_signing_secret(session, settings, integration_id=integration.id)
    if not verify_telegram(request.headers, raw_body, secret):
        # Iron rule: a tampered/absent signature must do NO relay/LLM/outbound
        # work. Nothing below this line has executed yet.
        raise AuthenticationError("invalid telegram signature")
    payload = _parse_json(raw_body)
    # RAGZ-PUB-10: Telegram's secret-token header (verify_telegram) has no
    # timestamp bound into it -- a captured valid delivery stays replayable
    # for as long as the secret is unrotated, not just a signature-skew
    # window -- so a TTL-bounded claim (like Slack/Discord's below) would
    # only delay a replay, not stop it. `update_id` is monotonically
    # increasing per bot, so a persistent high-water mark (claim_monotonic)
    # is used instead: any update_id at or below the highest ever seen for
    # this integration is rejected, with no expiry for a replay to outlive.
    # Tradeoff (documented, accepted): this also drops a legitimately
    # out-of-order-but-lower update_id arriving after a higher one -- rare
    # for Telegram's own delivery order, and preferable to being replayable
    # forever. Claimed before any parse/relay work; a losing claim still
    # 200s (Telegram's expected ack) so Telegram stops retrying, but does no
    # LLM/outbound work.
    update_id = payload.get("update_id")
    if isinstance(update_id, int):
        key = f"webhook_hwm:telegram:{integration.webhook_id}"
        if not await claim_monotonic(
            request.app.state.redis, key, update_id, _TELEGRAM_HWM_TTL_SECONDS
        ):
            structlog.get_logger().info(
                "webhook_replay_ignored", platform="telegram",
                webhook_id=str(integration.webhook_id), unique_id=update_id,
            )
            return {"ok": True}
    else:
        # Fail safe rather than fail closed: an update shape with no
        # update_id (shouldn't happen for real Telegram traffic) skips dedup
        # rather than crashing the route.
        structlog.get_logger().info(
            "webhook_dedup_skipped_no_id", platform="telegram",
            webhook_id=str(integration.webhook_id),
        )
    parsed = platforms.parse_telegram_update(payload)
    if parsed is None:
        return {"ok": True}  # non-text update (e.g. /start, a sticker) -- nothing to relay
    external_chat_id, text = parsed
    answer = await answer_for_integration(
        request, session, settings, integration, external_chat_id=external_chat_id, text=text,
    )
    token = await bots_service.get_token(session, settings, integration_id=integration.id)
    await platforms.send_telegram(
        token=token, chat_id=external_chat_id, text=answer,
        transport=request.app.state.bot_outbound_transport,
    )
    return {"ok": True}


async def _relay_and_send_slack(
    request: Request, settings: Settings, integration_id: UUID, external_chat_id: str, text: str,
) -> None:
    """Runs AFTER the 200 ack is sent (BackgroundTasks) -- the request's own
    `session` dependency is already closed by then (see api/routes/
    models.py::_background_sync's identical rationale), so this opens a
    FRESH session from app.state.session_factory. `request` itself (and
    request.app.state) is still valid to read here -- only the per-request
    DB session dependency is scoped to the response lifecycle, not the
    Request/App objects.
    """
    try:
        factory = request.app.state.session_factory
        async with factory() as session:
            integration = await bots_service.get_integration(
                session, integration_id=integration_id
            )
            answer = await answer_for_integration(
                request, session, settings, integration,
                external_chat_id=external_chat_id, text=text,
            )
            token = await bots_service.get_token(
                session, settings, integration_id=integration.id
            )
        await platforms.send_slack(
            token=token, channel=external_chat_id, text=answer,
            transport=request.app.state.bot_outbound_transport,
        )
    except UpstreamError:
        pass  # the platform-side send/relay failed; nothing left to notify the caller with
    except Exception:
        # Never let an unexpected error escape a background task -- the 200
        # ack is already on the wire, so an unhandled raise here can't
        # surface to Slack. structlog is the only place this is observable.
        structlog.get_logger().warning("slack_background_relay_failed", exc_info=True)


@router.post("/slack/{webhook_id}", dependencies=[_bot_webhook_ip_limit])
async def slack_webhook(
    webhook_id: UUID, request: Request, session: SessionDep, settings: SettingsDep,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    # Same 404-before-anything-else shape as telegram_webhook: resolve_by_
    # webhook 404s on unknown/wrong-platform/disabled ids alike, so probing
    # random UUIDs learns nothing about which are real.
    integration = await bots_service.resolve_by_webhook(
        session, platform="slack", webhook_id=webhook_id
    )
    await _rate_limit_bot_webhook(request, integration.id)
    # Raw bytes read ONCE and reused for verify + json-parse below -- see
    # telegram_webhook's identical rationale (including the body-size cap).
    raw_body = await _read_bounded_body(request)
    secret = await bots_service.get_signing_secret(session, settings, integration_id=integration.id)
    if not verify_slack(request.headers, raw_body, secret):
        # Iron rule: a tampered/absent/stale signature must do NO relay/LLM/
        # outbound work -- not even echoing a url_verification challenge.
        # Nothing below this line, including the handshake below, has run.
        raise AuthenticationError("invalid slack signature")
    payload = _parse_json(raw_body)
    if payload.get("type") == "url_verification":
        # Handshake stays exempt from dedup (it's not a chat delivery, and
        # Slack may legitimately re-probe it during setup) -- but it's still
        # gated behind the signature check above, unchanged.
        return {"challenge": payload.get("challenge")}
    parsed = platforms.parse_slack_event(payload)
    if parsed is None:
        return {"ok": True}  # non-message event, or our own bot's message (loop guard)
    # RAGZ-PUB-10: Slack's own 5-minute timestamp-skew check (verify_slack)
    # bounds how long a captured signature stays valid, but a replay WITHIN
    # that window still isn't caught by signature verification alone --
    # `event_id` (unique per Events API delivery) closes that gap. A losing
    # claim still acks 200 (so Slack's own retry-on-non-200 behavior doesn't
    # kick in) but skips the background relay/outbound task entirely.
    event_id = payload.get("event_id")
    if isinstance(event_id, str) and event_id:
        key = f"webhook_seen:slack:{integration.webhook_id}:{event_id}"
        if not await _claim_webhook_delivery(request, key):
            structlog.get_logger().info(
                "webhook_replay_ignored", platform="slack",
                webhook_id=str(integration.webhook_id), unique_id=event_id,
            )
            return {"ok": True}
    else:
        structlog.get_logger().info(
            "webhook_dedup_skipped_no_id", platform="slack",
            webhook_id=str(integration.webhook_id),
        )
    external_chat_id, text = parsed
    # Ack within Slack's sub-3-second Events API window; the actual RAG
    # relay + outbound send happen in the background after this returns.
    background_tasks.add_task(
        _relay_and_send_slack, request, settings, integration.id, external_chat_id, text,
    )
    return {"ok": True}


async def _relay_and_send_discord(
    request: Request, settings: Settings, integration_id: UUID, external_chat_id: str, text: str,
    application_id: str, interaction_token: str,
) -> None:
    """Mirrors _relay_and_send_slack -- see its docstring for the fresh-
    session/background-task rationale. Posts the real answer as the
    deferred interaction's followup (PATCH .../messages/@original), matching
    the type-5 DEFERRED ack discord_webhook already sent synchronously."""
    try:
        factory = request.app.state.session_factory
        async with factory() as session:
            integration = await bots_service.get_integration(
                session, integration_id=integration_id
            )
            answer = await answer_for_integration(
                request, session, settings, integration,
                external_chat_id=external_chat_id, text=text,
            )
        await platforms.send_discord(
            application_id=application_id, interaction_token=interaction_token, text=answer,
            transport=request.app.state.bot_outbound_transport,
        )
    except UpstreamError:
        pass  # the platform-side send/relay failed; nothing left to notify the caller with
    except Exception:
        structlog.get_logger().warning("discord_background_relay_failed", exc_info=True)


@router.post("/discord/{webhook_id}", dependencies=[_bot_webhook_ip_limit])
async def discord_webhook(
    webhook_id: UUID, request: Request, session: SessionDep, settings: SettingsDep,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    integration = await bots_service.resolve_by_webhook(
        session, platform="discord", webhook_id=webhook_id
    )
    await _rate_limit_bot_webhook(request, integration.id)
    # Bounded to _WEBHOOK_BODY_MAX_BYTES (413 if exceeded) before Ed25519
    # verification -- see telegram_webhook's identical rationale.
    raw_body = await _read_bounded_body(request)
    secret = await bots_service.get_signing_secret(session, settings, integration_id=integration.id)
    if not verify_discord(request.headers, raw_body, secret):
        # Iron rule: verify Ed25519 FIRST -- not even PING/PONG runs before
        # this. Nothing below this line has executed yet.
        raise AuthenticationError("invalid discord signature")
    payload = _parse_json(raw_body)
    if payload.get("type") == 1:  # PING
        # Handshake stays exempt from dedup (Discord re-PINGs during setup
        # verification) -- but it's still gated behind the signature check
        # above, unchanged.
        return {"type": 1}  # PONG
    parsed = platforms.parse_discord_interaction(payload)
    if parsed is None:
        return {"type": 4, "data": {"content": "Sorry, I couldn't understand that command."}}
    # RAGZ-PUB-10: verify_discord now rejects a stale X-Signature-Timestamp
    # (see its docstring), but a replay delivered WITHIN that freshness
    # window still passes signature verification -- `id` (the interaction's
    # own unique snowflake) closes that gap. A losing claim still acks with
    # the same deferred response Discord expects (so it doesn't retry) but
    # skips the background relay/followup task entirely.
    interaction_id = payload.get("id")
    if isinstance(interaction_id, str) and interaction_id:
        key = f"webhook_seen:discord:{integration.webhook_id}:{interaction_id}"
        if not await _claim_webhook_delivery(request, key):
            structlog.get_logger().info(
                "webhook_replay_ignored", platform="discord",
                webhook_id=str(integration.webhook_id), unique_id=interaction_id,
            )
            return {"type": 5}
    else:
        structlog.get_logger().info(
            "webhook_dedup_skipped_no_id", platform="discord",
            webhook_id=str(integration.webhook_id),
        )
    external_chat_id, text = parsed
    application_id = str(payload.get("application_id", ""))
    interaction_token = str(payload.get("token", ""))
    background_tasks.add_task(
        _relay_and_send_discord, request, settings, integration.id, external_chat_id, text,
        application_id, interaction_token,
    )
    return {"type": 5}  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE -- ack within Discord's 3s window
