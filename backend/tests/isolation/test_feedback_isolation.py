"""Adversarial isolation test for the admin feedback queue (iron rule 1).

Org B (an admin in a different org) must see NONE of org A's feedback
through GET /admin/feedback -- the org scoping in list_feedback_queue is
this plan's one tenant-isolation-relevant surface.
"""

import json
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


async def test_admin_feedback_queue_is_org_scoped(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User, org_b_user: User,
) -> None:
    h_a = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session,
                                        seeded_superadmin, h_a)
    r = await chat_client.post(f"/api/v1/chats/{chat_id}/messages",
                               json={"content": "secret question"}, headers=h_a)
    done = [b for b in r.text.strip().split("\n\n") if "event: done" in b][0]
    message_id = json.loads(done.split("data: ", 1)[1])["message_id"]

    r_fb = await chat_client.put(f"/api/v1/messages/{message_id}/feedback",
                                 json={"rating": "down"}, headers=h_a)
    assert r_fb.status_code == 200

    # Org A's own admin DOES see it -- proves this isn't just empty everywhere.
    r_a = await chat_client.get("/api/v1/admin/feedback", headers=h_a)
    assert r_a.status_code == 200
    assert [item["message_id"] for item in r_a.json()["items"]] == [message_id]

    # Org B is a different org entirely -- an adversarial cross-org admin.
    h_b = await auth(chat_client, "b@rival.com")
    r = await chat_client.get("/api/v1/admin/feedback", headers=h_b)
    assert r.status_code == 200
    assert r.json()["items"] == []  # org B sees none of org A's feedback
