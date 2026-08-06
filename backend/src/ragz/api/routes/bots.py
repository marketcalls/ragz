"""Inbound chat-platform webhooks (design doc §Components item 6): verify ->
handshake/parse -> relay -> outbound. No FastAPI auth dependency here -- the
platform itself is the caller; signature verification IS the auth, and it
happens BEFORE any workspace/LLM work (iron rule). Mounted /external/bots.
Slack + Discord routes are added in Task 6; this file starts with Telegram."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.bots_relay import answer_for_integration
from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthenticationError, BadRequestError
from ragz.core.ratelimit import check_rate_limit
from ragz.modules.bots import platforms
from ragz.modules.bots import service as bots_service
from ragz.modules.bots.verify import verify_telegram

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


@router.post("/telegram/{webhook_id}")
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
