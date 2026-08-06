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
from ragz.core.errors import AuthenticationError, BadRequestError, UpstreamError
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


async def _rate_limit_bot_webhook(request: Request, integration_id: UUID) -> None:
    await check_rate_limit(
        request.app.state.redis, f"rl:bot_webhook:integration:{integration_id}",
        _INBOUND_LIMIT, _INBOUND_WINDOW_SECONDS,
    )


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
    # computed over the ORIGINAL bytes Telegram sent.
    raw_body = await request.body()
    secret = await bots_service.get_signing_secret(session, settings, integration_id=integration.id)
    if not verify_telegram(request.headers, raw_body, secret):
        # Iron rule: a tampered/absent signature must do NO relay/LLM/outbound
        # work. Nothing below this line has executed yet.
        raise AuthenticationError("invalid telegram signature")
    payload = _parse_json(raw_body)
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
    # telegram_webhook's identical rationale.
    raw_body = await request.body()
    secret = await bots_service.get_signing_secret(session, settings, integration_id=integration.id)
    if not verify_slack(request.headers, raw_body, secret):
        # Iron rule: a tampered/absent/stale signature must do NO relay/LLM/
        # outbound work -- not even echoing a url_verification challenge.
        # Nothing below this line, including the handshake below, has run.
        raise AuthenticationError("invalid slack signature")
    payload = _parse_json(raw_body)
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    parsed = platforms.parse_slack_event(payload)
    if parsed is None:
        return {"ok": True}  # non-message event, or our own bot's message (loop guard)
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
    raw_body = await request.body()
    secret = await bots_service.get_signing_secret(session, settings, integration_id=integration.id)
    if not verify_discord(request.headers, raw_body, secret):
        # Iron rule: verify Ed25519 FIRST -- not even PING/PONG runs before
        # this. Nothing below this line has executed yet.
        raise AuthenticationError("invalid discord signature")
    payload = _parse_json(raw_body)
    if payload.get("type") == 1:  # PING
        return {"type": 1}  # PONG
    parsed = platforms.parse_discord_interaction(payload)
    if parsed is None:
        return {"type": 4, "data": {"content": "Sorry, I couldn't understand that command."}}
    external_chat_id, text = parsed
    application_id = str(payload.get("application_id", ""))
    interaction_token = str(payload.get("token", ""))
    background_tasks.add_task(
        _relay_and_send_discord, request, settings, integration.id, external_chat_id, text,
        application_id, interaction_token,
    )
    return {"type": 5}  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE -- ack within Discord's 3s window
