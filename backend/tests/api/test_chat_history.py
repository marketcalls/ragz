from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from tests.api.test_chat_stream import auth, make_model_and_chat

# chat_client/fake_streamer fixtures live in test_chat_stream; pytest only
# shares fixtures across modules via conftest.py or an explicit plugin import.
pytest_plugins = ["tests.api.test_chat_stream"]


async def test_history_crud_and_tree_shape(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)

    await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                           json={"content": "v1?"}, headers=h)
    await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                           json={"content": "v2?", "parent_message_id": None}, headers=h)

    r = await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h)
    assert r.status_code == 200
    tree = r.json()
    assert tree["id"] == chat_id
    roots = tree["messages"]
    assert [m["sibling_index"] for m in roots] == [0, 1]
    assert [m["content"] for m in roots] == ["v1?", "v2?"]
    for root in roots:
        assert root["role"] == "user" and root["parent_message_id"] is None
        assert len(root["children"]) == 1
        child = root["children"][0]
        assert child["role"] == "assistant"
        assert child["parent_message_id"] == root["id"]
        assert child["children"] == []
        assert [c["marker"] for c in child["citations"]] == [1]

    r = await chat_client.patch(f"/api/v1/chats/{chat_id}",
                                json={"title": "Renamed"}, headers=h)
    assert r.json()["title"] == "Renamed"
    listing = await chat_client.get("/api/v1/chats", headers=h)
    assert [c["title"] for c in listing.json()] == ["Renamed"]
    assert (await chat_client.delete(f"/api/v1/chats/{chat_id}", headers=h)).status_code == 204
    assert (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h)).status_code == 404


async def test_send_rate_limited_per_user(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    for _ in range(30):
        r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                                   json={"content": "hi"}, headers=h)
        assert r.status_code == 200
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "hi"}, headers=h)
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/problem+json")
