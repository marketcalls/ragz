"""Functional tests for the admin feedback queue filters.

The queue now defaults to ALL ratings (was down-only) and supports rating,
author (user_id), and half-open date-range filters. Org-scoping itself is
covered by tests/isolation/test_feedback_isolation.py.
"""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.chat.models import MessageFeedback
from ragz.modules.tenancy.models import WorkspaceMember
from tests.api.test_chat_stream import auth, make_model_and_chat

# chat_client/fake_streamer fixtures live in test_chat_stream (see the sibling
# isolation test for the same shared-fixture pattern).
pytest_plugins = ["tests.api.test_chat_stream"]


async def _send_and_rate(
    client: httpx.AsyncClient, chat_id: str, headers: dict[str, str],
    question: str, rating: str,
) -> str:
    r = await client.post(f"/api/v1/chats/{chat_id}/messages",
                          json={"content": question}, headers=headers)
    done = [b for b in r.text.strip().split("\n\n") if "event: done" in b][0]
    message_id: str = json.loads(done.split("data: ", 1)[1])["message_id"]
    r_fb = await client.put(f"/api/v1/messages/{message_id}/feedback",
                            json={"rating": rating}, headers=headers)
    assert r_fb.status_code == 200
    return message_id


async def test_feedback_queue_rating_filter_and_author(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    up_id = await _send_and_rate(chat_client, chat_id, h, "positive one", "up")
    down_id = await _send_and_rate(chat_client, chat_id, h, "negative one", "down")

    # No rating param => BOTH ratings (default changed from down-only to all).
    r = await chat_client.get("/api/v1/admin/feedback", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["message_id"] for i in items} == {up_id, down_id}
    # The author's identity is surfaced on every row.
    assert all(i["user_email"] == "a@acme.com" for i in items)
    assert all(i["user_id"] == str(seeded_user.id) for i in items)

    # rating=up / rating=down narrow to a single row each.
    r_up = await chat_client.get("/api/v1/admin/feedback", params={"rating": "up"}, headers=h)
    assert [i["message_id"] for i in r_up.json()["items"]] == [up_id]
    r_down = await chat_client.get("/api/v1/admin/feedback", params={"rating": "down"}, headers=h)
    assert [i["message_id"] for i in r_down.json()["items"]] == [down_id]


async def test_feedback_queue_user_filter(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h_a = await auth(chat_client, "a@acme.com")
    chat_a = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h_a)
    await _send_and_rate(chat_client, chat_a, h_a, "from a", "down")

    # A second author in the SAME org, member of the same workspace.
    ws = chat_env["workspace"]
    user_b = User(org_id=seeded_user.org_id, email="b2@acme.com",
                  password_hash=hash_password("pw123456"), role="admin")
    session.add(user_b)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user_b.id))
    await session.commit()

    h_b = await auth(chat_client, "b2@acme.com")
    r_chat_b = await chat_client.post(
        "/api/v1/chats", json={"workspace_id": str(ws.id)}, headers=h_b)
    chat_b = str(r_chat_b.json()["id"])
    b_msg = await _send_and_rate(chat_client, chat_b, h_b, "from b", "down")

    # Filtering by author == user_b returns only b's feedback (a's is dropped).
    r = await chat_client.get(
        "/api/v1/admin/feedback", params={"user_id": str(user_b.id)}, headers=h_a)
    items = r.json()["items"]
    assert [i["message_id"] for i in items] == [b_msg]
    assert items[0]["user_email"] == "b2@acme.com"


async def test_feedback_queue_date_range_filter(
    chat_client: httpx.AsyncClient, chat_env: dict[str, Any], session: AsyncSession,
    seeded_user: User, seeded_superadmin: User,
) -> None:
    h = await auth(chat_client, "a@acme.com")
    chat_id = await make_model_and_chat(chat_client, chat_env, session, seeded_superadmin, h)
    old_id = await _send_and_rate(chat_client, chat_id, h, "old one", "down")
    new_id = await _send_and_rate(chat_client, chat_id, h, "new one", "down")

    # Pin the two feedbacks onto known, distinct days.
    await session.execute(
        update(MessageFeedback).where(MessageFeedback.message_id == UUID(old_id))
        .values(created_at=datetime(2026, 1, 1, 12, 0, 0)))
    await session.execute(
        update(MessageFeedback).where(MessageFeedback.message_id == UUID(new_id))
        .values(created_at=datetime(2026, 6, 15, 12, 0, 0)))
    await session.commit()

    # Half-open [start, end): the client sends end as T23:59:59.999 so the whole
    # end day is included. A one-day window on Jan 1 catches only the old row.
    r = await chat_client.get(
        "/api/v1/admin/feedback",
        params={"start": "2026-01-01T00:00:00", "end": "2026-01-01T23:59:59.999"}, headers=h)
    assert [i["message_id"] for i in r.json()["items"]] == [old_id]

    # A start-only filter after Jan keeps only the newer row.
    r2 = await chat_client.get(
        "/api/v1/admin/feedback", params={"start": "2026-02-01T00:00:00"}, headers=h)
    assert [i["message_id"] for i in r2.json()["items"]] == [new_id]
