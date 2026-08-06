import json

import httpx
import pytest

from ragz.core.errors import UpstreamError
from ragz.modules.bots import platforms


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_send_telegram_success_and_failure() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    await platforms.send_telegram(
        token="123:ABC", chat_id="42", text="hi", transport=_transport(handler)  # noqa: S106
    )
    assert calls[0].url.path == "/bot123:ABC/sendMessage"
    assert json.loads(calls[0].content) == {"chat_id": "42", "text": "hi"}

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "bad chat id"})

    with pytest.raises(UpstreamError):
        await platforms.send_telegram(
            token="123:ABC", chat_id="42", text="hi", transport=_transport(fail_handler)  # noqa: S106
        )


async def test_send_slack_success_and_ok_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer xoxb-tok"
        return httpx.Response(200, json={"ok": True})

    await platforms.send_slack(
        token="xoxb-tok", channel="C1", text="hi", transport=_transport(handler)  # noqa: S106
    )

    def not_ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(UpstreamError):
        await platforms.send_slack(
            token="xoxb-tok", channel="bad", text="hi", transport=_transport(not_ok_handler)  # noqa: S106
        )


async def test_send_discord_success_and_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v10/webhooks/app1/tok1/messages/@original"
        return httpx.Response(200, json={})

    await platforms.send_discord(
        application_id="app1", interaction_token="tok1", text="hi",  # noqa: S106
        transport=_transport(handler),
    )

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Unknown interaction"})

    with pytest.raises(UpstreamError):
        await platforms.send_discord(
            application_id="app1", interaction_token="tok1", text="hi",  # noqa: S106
            transport=_transport(fail_handler),
        )


def test_parse_telegram_update() -> None:
    assert platforms.parse_telegram_update(
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "hello"}}
    ) == ("42", "hello")
    # e.g. edited_message, no text
    assert platforms.parse_telegram_update({"update_id": 1}) is None
    # no text (sticker etc.)
    assert platforms.parse_telegram_update({"message": {"chat": {"id": 42}}}) is None


def test_parse_slack_event() -> None:
    assert platforms.parse_slack_event(
        {"type": "event_callback", "event": {"type": "message", "channel": "C1", "text": "hi"}}
    ) == ("C1", "hi")
    assert platforms.parse_slack_event(
        {
            "type": "event_callback",
            "event": {"type": "message", "channel": "C1", "text": "loop", "bot_id": "B1"},
        }
    ) is None  # our own reply -- loop guard
    assert platforms.parse_slack_event({"type": "url_verification", "challenge": "x"}) is None


def test_parse_discord_interaction() -> None:
    assert platforms.parse_discord_interaction(
        {
            "type": 2, "channel_id": "C1", "application_id": "app1", "token": "tok1",
            "data": {
                "name": "ask",
                "options": [{"name": "question", "type": 3, "value": "what is x"}],
            },
        }
    ) == ("C1", "what is x")
    assert platforms.parse_discord_interaction({"type": 1}) is None  # PING
    # no options
    assert platforms.parse_discord_interaction({"type": 2, "channel_id": "C1", "data": {}}) is None
