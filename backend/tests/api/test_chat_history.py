from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
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
        # CHAT-4: section/version ride the persisted CitationOut too (no
        # section on FakeRetriever's default chunks; document defaults to v1).
        assert child["citations"][0]["section"] is None
        assert child["citations"][0]["version"] == 1

    r = await chat_client.patch(f"/api/v1/chats/{chat_id}",
                                json={"title": "Renamed"}, headers=h)
    assert r.json()["title"] == "Renamed"
    listing = await chat_client.get("/api/v1/chats", headers=h)
    assert [c["title"] for c in listing.json()] == ["Renamed"]
    assert (await chat_client.delete(f"/api/v1/chats/{chat_id}", headers=h)).status_code == 204
    assert (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h)).status_code == 404


async def test_list_chats_filtered_by_workspace(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    """Plan D's sidebar filters chats by workspace: an optional workspace_id
    query param on GET /api/v1/chats narrows the (still org+user scoped) list."""
    ws2 = Workspace(org_id=seeded_user.org_id, name="OtherWS")
    session.add(ws2)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws2.id, user_id=seeded_user.id))
    await session.commit()

    h = await auth(chat_client, "a@acme.com")
    r1 = await chat_client.post(
        "/api/v1/chats", json={"workspace_id": str(chat_env["workspace"].id)}, headers=h
    )
    r2 = await chat_client.post(
        "/api/v1/chats", json={"workspace_id": str(ws2.id)}, headers=h
    )
    assert r1.status_code == r2.status_code == 201
    chat1_id, chat2_id = r1.json()["id"], r2.json()["id"]

    filtered = await chat_client.get(
        "/api/v1/chats", params={"workspace_id": str(ws2.id)}, headers=h
    )
    assert filtered.status_code == 200
    assert [c["id"] for c in filtered.json()] == [chat2_id]

    unfiltered = await chat_client.get("/api/v1/chats", headers=h)
    assert {c["id"] for c in unfiltered.json()} == {chat1_id, chat2_id}


async def test_message_feedback_round_trip_and_appears_in_chat_tree(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)

    await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                           json={"content": "v1?"}, headers=h)

    tree = (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h)).json()
    root = tree["messages"][0]
    assert root["feedback"] is None
    message_id = root["children"][0]["id"]  # assistant reply
    assert root["children"][0]["feedback"] is None

    r = await chat_client.put(
        f"/api/v1/messages/{message_id}/feedback",
        json={"rating": "down", "comment": "wrong citation"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json() == {"rating": "down", "comment": "wrong citation"}

    tree = (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h)).json()
    msg = next(m for m in tree["messages"][0]["children"] if m["id"] == message_id)
    assert msg["feedback"] == {"rating": "down", "comment": "wrong citation"}

    # Switching rating overwrites, not duplicates.
    r2 = await chat_client.put(
        f"/api/v1/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers=h,
    )
    assert r2.json() == {"rating": "up", "comment": None}

    # DELETE clears it entirely.
    assert (
        await chat_client.delete(f"/api/v1/messages/{message_id}/feedback", headers=h)
    ).status_code == 204
    tree2 = (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h)).json()
    msg2 = next(m for m in tree2["messages"][0]["children"] if m["id"] == message_id)
    assert msg2["feedback"] is None


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
