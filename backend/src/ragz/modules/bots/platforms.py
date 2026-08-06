"""SDK-free httpx outbound senders + inbound payload parsers (mirrors
LiteLLMEmbedder's SDK-free approach). transport is injectable so tests never
hit the network -- production callers (api/routes/bots.py) pass
request.app.state.bot_outbound_transport (None in prod = real network)."""

import httpx

from ragz.core.errors import UpstreamError

_TELEGRAM_API_BASE = "https://api.telegram.org"
_SLACK_API_BASE = "https://slack.com/api"
_DISCORD_API_BASE = "https://discord.com/api/v10"
_TIMEOUT_SECONDS = 10.0


async def send_telegram(
    *, token: str, chat_id: str, text: str, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text}
        )
    if response.status_code >= 400 or response.json().get("ok") is False:
        raise UpstreamError(f"telegram sendMessage failed: {response.status_code}")


async def send_slack(
    *, token: str, channel: str, text: str, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{_SLACK_API_BASE}/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": text},
        )
    if response.status_code >= 400 or not response.json().get("ok", False):
        raise UpstreamError(f"slack chat.postMessage failed: {response.status_code}")


async def send_discord(
    *, application_id: str, interaction_token: str, text: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """PATCHes the deferred interaction's original response (the
    "@original" followup) -- see api/routes/bots.py's discord_webhook, which
    always acks with a type-5 deferred response before this call."""
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
        response = await client.patch(
            f"{_DISCORD_API_BASE}/webhooks/{application_id}/{interaction_token}/messages/@original",
            json={"content": text},
        )
    if response.status_code >= 400:
        raise UpstreamError(f"discord followup message failed: {response.status_code}")


def parse_telegram_update(payload: dict[str, object]) -> tuple[str, str] | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str) or not text:
        return None
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    return str(chat_id), text


def parse_slack_event(payload: dict[str, object]) -> tuple[str, str] | None:
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    if event.get("type") != "message" or event.get("bot_id") is not None:
        return None  # ignore non-message events and our own bot's replies (loop guard)
    channel = event.get("channel")
    text = event.get("text")
    if not isinstance(channel, str) or not isinstance(text, str) or not text:
        return None
    return channel, text


def parse_discord_interaction(payload: dict[str, object]) -> tuple[str, str] | None:
    if payload.get("type") != 2:  # not an APPLICATION_COMMAND interaction
        return None
    channel_id = payload.get("channel_id")
    data = payload.get("data")
    if not isinstance(channel_id, str) or not isinstance(data, dict):
        return None
    options = data.get("options")
    if not isinstance(options, list) or not options or not isinstance(options[0], dict):
        return None
    value = options[0].get("value")
    if not isinstance(value, str) or not value:
        return None
    return channel_id, value
