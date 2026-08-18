from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.auth.models import User
from ragz.modules.chat import service
from ragz.modules.chat.models import Chat
from ragz.modules.chat.service import (
    active_leaf,
    add_message,
    create_chat,
    delete_chat,
    get_chat,
    list_chats,
    list_messages,
    rename_chat,
)
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.conftest import FakeCompleter


@pytest.fixture
async def ctx(
    session: AsyncSession, seeded_user: User, chat_env: dict[str, Any]
) -> TenantContext:
    """TenantContext for chat_env's seeded workspace member (mirrors
    test_agent.py's ctx fixture — this file uses chat_env's Document-bearing
    workspace rather than make_ctx's bare one for the new audit tests below)."""
    ws = chat_env["workspace"]
    return TenantContext(
        user_id=seeded_user.id, org_id=seeded_user.org_id, role=seeded_user.role,
        workspace_ids=frozenset({ws.id}),
    )


async def make_ctx(session: AsyncSession, user: User) -> tuple[TenantContext, Workspace]:
    ws = Workspace(org_id=user.org_id, name="W")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
    await session.commit()
    ctx = TenantContext(user_id=user.id, org_id=user.org_id, role=user.role,
                        workspace_ids=frozenset({ws.id}))
    return ctx, ws


async def build_turn(
    session: AsyncSession, ctx: TenantContext, chat: Chat, q: str, a: str, parent: object
) -> tuple[object, object]:
    user_msg = await add_message(session, ctx, chat, role="user", content=q, parent=parent)
    asst = await add_message(session, ctx, chat, role="assistant", content=a, parent=user_msg)
    return user_msg, asst


async def test_crud_and_scoping(session: AsyncSession, seeded_user: User) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    assert [c.id for c in await list_chats(session, ctx)] == [chat.id]
    renamed = await rename_chat(session, ctx, chat.id, "Q3 numbers")
    assert renamed.title == "Q3 numbers"

    other = TenantContext(user_id=ws.id, org_id=ctx.org_id, role="user",
                          workspace_ids=frozenset())
    with pytest.raises(NotFoundError):
        await get_chat(session, other, chat.id)  # another user never sees it

    await delete_chat(session, ctx, chat.id)
    assert await list_chats(session, ctx) == []


async def test_alternation_and_dense_siblings(
    session: AsyncSession, seeded_user: User
) -> None:
    """Verify message role alternation and dense sibling indices.

    Note: sibling_index density is protected by a row-level lock on the chat row
    (with_for_update()) in add_message, which serializes concurrent index computation.
    This test exercises the sequential case; true concurrency is hard to test reliably.
    """
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    u1, a1 = await build_turn(session, ctx, chat, "q1", "a1", parent=None)

    with pytest.raises(ConflictError):  # user under user
        await add_message(session, ctx, chat, role="user", content="x", parent=u1)
    with pytest.raises(ConflictError):  # assistant at root
        await add_message(session, ctx, chat, role="assistant", content="x", parent=None)

    # Edit-and-resend: new user sibling at the ROOT gets the next index.
    u1b = await add_message(session, ctx, chat, role="user", content="q1 v2", parent=None)
    assert (u1b.sibling_index, u1.sibling_index) == (1, 0)
    # Regenerate: second assistant under the same user message.
    a1b = await add_message(session, ctx, chat, role="assistant", content="a1 v2", parent=u1)
    assert a1b.sibling_index == 1


async def test_active_leaf_follows_newest_siblings(
    session: AsyncSession, seeded_user: User
) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    u1, a1 = await build_turn(session, ctx, chat, "q1", "a1", parent=None)
    u2, a2 = await build_turn(session, ctx, chat, "q2", "a2", parent=a1)
    # Edit q2 -> sibling branch with its own answer; it becomes the active path.
    u2b, a2b = await build_turn(session, ctx, chat, "q2 v2", "a2 v2", parent=a1)
    msgs = await list_messages(session, chat.id)
    leaf = active_leaf(msgs)
    assert leaf is not None and leaf.id == a2b.id


async def test_membership_required_for_create(
    session: AsyncSession, seeded_user: User
) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    stranger = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                             role="user", workspace_ids=frozenset())
    with pytest.raises(NotFoundError):
        await create_chat(session, stranger, workspace_id=ws.id)


async def test_first_root_message_sets_title_with_truncation(
    session: AsyncSession, seeded_user: User
) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)
    assert chat.title == "New chat"

    content = (
        "  What   is\nthe capital of France and why is it important for trade "
        "routes historically?  "
    )
    await add_message(session, ctx, chat, role="user", content=content, parent=None)
    assert chat.title == "What is the capital of France and why is it…"


async def test_first_root_message_sets_title_short_case(
    session: AsyncSession, seeded_user: User
) -> None:
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)

    u1 = await add_message(session, ctx, chat, role="user", content="Hi there", parent=None)
    assert chat.title == "Hi there"

    # A child (assistant) message never re-titles the chat.
    await add_message(session, ctx, chat, role="assistant", content="Hello!", parent=u1)
    assert chat.title == "Hi there"


async def test_second_root_sibling_does_not_retitle(
    session: AsyncSession, seeded_user: User
) -> None:
    """An edit-and-resend of the first message creates a second ROOT sibling
    (sibling_index == 1); it must not overwrite the title set by the first."""
    ctx, ws = await make_ctx(session, seeded_user)
    chat = await create_chat(session, ctx, workspace_id=ws.id)

    await add_message(session, ctx, chat, role="user", content="Original question", parent=None)
    assert chat.title == "Original question"

    await add_message(
        session, ctx, chat, role="user", content="Completely different edited question",
        parent=None,
    )
    assert chat.title == "Original question"


async def test_audit_message_no_utility_model_is_noop(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    chat = await service.create_chat(session, ctx, workspace_id=chat_env["workspace"].id)
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content="q", parent=None
    )
    msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_ASSISTANT, content="[1] answer",
        parent=user_msg, grounding="documents",
    )
    assert await service.audit_message(session, msg.id) is False
    await session.refresh(msg)
    assert msg.grounding_score is None


async def test_audit_message_skips_non_document_grounding(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext, utility_model: object,
) -> None:
    chat = await service.create_chat(session, ctx, workspace_id=chat_env["workspace"].id)
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content="hi", parent=None
    )
    msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_ASSISTANT, content="hello",
        parent=user_msg, grounding="documents",
    )
    msg.no_answer = True  # decline turns carry grounding="documents" but nothing to audit
    await session.commit()
    assert await service.audit_message(session, msg.id) is False


async def test_audit_message_persists_scores(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext, utility_model: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ragz.modules.chat import audit as chat_audit
    from ragz.modules.chat.llm import LLMCompletion, LLMUsage

    fake = FakeCompleter([LLMCompletion(
        text='{"grounding_score": 0.8, "completeness_score": 0.9}', tool_calls=[],
        usage=LLMUsage(prompt_tokens=30, completion_tokens=10),
    )])
    # Patched on chat.audit, not chat.service: audit_message resolves
    # _completer_for_audit in its OWN module namespace, so the re-export on
    # service.py is not the seam even though the public name still lives there.
    monkeypatch.setattr(chat_audit, "_completer_for_audit", lambda settings: fake)

    chat = await service.create_chat(session, ctx, workspace_id=chat_env["workspace"].id)
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content="Where is the muster point?",
        parent=None,
    )
    msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_ASSISTANT, content="[1] Gate B.",
        parent=user_msg, grounding="documents",
    )
    assert await service.audit_message(session, msg.id) is True
    await session.refresh(msg)
    assert msg.grounding_score == 0.8 and msg.completeness_score == 0.9


async def test_answer_quality_summary_averages_and_ranks_worst(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    chat = await service.create_chat(session, ctx, workspace_id=chat_env["workspace"].id)
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content="q", parent=None
    )
    good = await service.add_message(
        session, ctx, chat, role=service.ROLE_ASSISTANT, content="good", parent=user_msg,
        grounding="documents",
    )
    good.grounding_score, good.completeness_score = 0.95, 0.9
    bad = await service.add_message(
        session, ctx, chat, role=service.ROLE_ASSISTANT, content="bad", parent=user_msg,
        grounding="documents",
    )
    bad.grounding_score, bad.completeness_score = 0.1, 0.2
    await session.commit()

    summary = await service.answer_quality_summary(session, ctx, days=30)
    assert summary.audited_count == 2
    assert summary.avg_grounding_score == pytest.approx((0.95 + 0.1) / 2)
    assert summary.low_score_count == 1
    assert summary.worst[0].message_id == bad.id  # lowest average first


async def test_answer_quality_summary_excludes_unaudited(
    session: AsyncSession, chat_env: dict[str, Any], ctx: TenantContext
) -> None:
    chat = await service.create_chat(session, ctx, workspace_id=chat_env["workspace"].id)
    user_msg = await service.add_message(
        session, ctx, chat, role=service.ROLE_USER, content="q", parent=None
    )
    await service.add_message(
        session, ctx, chat, role=service.ROLE_ASSISTANT, content="unaudited", parent=user_msg,
        grounding="documents",
    )
    summary = await service.answer_quality_summary(session, ctx, days=30)
    assert summary.audited_count == 0 and summary.worst == []
