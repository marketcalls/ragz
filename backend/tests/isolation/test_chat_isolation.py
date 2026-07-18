"""Adversarial isolation tests for the chat tier (iron rules 1 and 2).

Org B must see NOTHING of org A's chats; same-org users must not see each
other's chats; workspace non-members must not chat against a workspace.
"""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.tenancy.models import Organization
from tests.api.test_chat_stream import auth, make_model_and_chat

# chat_client/fake_streamer fixtures live in test_chat_stream; pytest only
# shares fixtures across modules via conftest.py or an explicit plugin import.
pytest_plugins = ["tests.api.test_chat_stream"]


@pytest.fixture
async def org_b_user(session: AsyncSession) -> User:
    org = Organization(name="RivalCorp")
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email="b@rival.com",
                password_hash=hash_password("pw123456"), role="admin")
    session.add(user)
    await session.commit()
    return user


async def seeded_chat_with_message(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_superadmin: User,
) -> tuple[str, str, dict[str, str]]:
    h_a = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session,
                                        seeded_superadmin, h_a)
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "secret question"}, headers=h_a)
    import json
    done = [b for b in r.text.strip().split("\n\n") if "event: done" in b][0]
    message_id = json.loads(done.split("data: ", 1)[1])["message_id"]
    return chat_id, message_id, h_a


async def test_cross_org_chat_access_denied(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, org_b_user: User,
) -> None:
    chat_id, message_id, _ = await seeded_chat_with_message(
        chat_client, chat_env, session, seeded_superadmin
    )
    h_b = await auth(chat_client, "b@rival.com")
    assert (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h_b)).status_code == 404
    assert (await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                                   json={"content": "leak?"}, headers=h_b)).status_code == 404
    assert (await chat_client.post(f"/api/v1/messages/{message_id}/regenerate",
                                   headers=h_b)).status_code == 404
    assert (await chat_client.delete(f"/api/v1/chats/{chat_id}", headers=h_b)).status_code == 404
    assert (await chat_client.get("/api/v1/chats", headers=h_b)).json() == []
    # Org B cannot open a chat against org A's workspace either.
    r = await chat_client.post(
        "/api/v1/chats", json={"workspace_id": str(chat_env["workspace"].id)}, headers=h_b
    )
    assert r.status_code == 404


async def test_same_org_users_have_private_chats(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    chat_id, message_id, _ = await seeded_chat_with_message(
        chat_client, chat_env, session, seeded_superadmin
    )
    peer = User(org_id=seeded_user.org_id, email="peer@acme.com",
                password_hash=hash_password("pw123456"), role="user")
    session.add(peer)
    await session.commit()
    h_peer = await auth(chat_client, "peer@acme.com")
    assert (await chat_client.get(f"/api/v1/chats/{chat_id}", headers=h_peer)).status_code == 404
    assert (await chat_client.post(f"/api/v1/messages/{message_id}/regenerate",
                                   headers=h_peer)).status_code == 404
    # Non-member of the workspace cannot create a chat there.
    r = await chat_client.post(
        "/api/v1/chats", json={"workspace_id": str(chat_env["workspace"].id)},
        headers=h_peer,
    )
    assert r.status_code == 404
