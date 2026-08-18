"""Chat lifecycle: create, list, fetch, rename, delete.

Split out of chat/service.py (Phase 2 item 2 of the 2026-08-17 architecture
review). This is the base of the chat module's internal dependency order --
attachments and message persistence both call get_chat, so it has to move
first or extracting them would create an import cycle back into service.py.

Re-exported from chat.service; every existing caller keeps working unchanged.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.errors import NotFoundError
from ragz.modules.chat.models import Chat
from ragz.modules.tenancy import service as tenancy_service
from ragz.modules.tenancy.context import TenantContext

_TITLE_MAX_CHARS = 48


def _auto_title(content: str) -> str:
    """Derive a short, single-line chat title from the first user message.

    Collapses whitespace/newlines, then truncates to _TITLE_MAX_CHARS at a
    word boundary (falling back to a hard cut if there's no boundary),
    appending an ellipsis only when truncation actually happened.
    """
    text = " ".join(content.split())
    if len(text) <= _TITLE_MAX_CHARS:
        return text
    truncated = text[:_TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{truncated}…"


async def create_chat(
    session: AsyncSession, ctx: TenantContext, *, workspace_id: UUID, title: str | None = None
) -> Chat:
    await tenancy_service.get_workspace(session, ctx, workspace_id)
    chat = Chat(org_id=ctx.org_id, workspace_id=workspace_id, user_id=ctx.user_id)
    if title:
        chat.title = title
    session.add(chat)
    await session.commit()
    return chat


async def list_chats(
    session: AsyncSession, ctx: TenantContext, *, workspace_id: UUID | None = None
) -> list[Chat]:
    stmt = (
        select(Chat)
        .where(Chat.org_id == ctx.org_id, Chat.user_id == ctx.user_id)
        .order_by(Chat.updated_at.desc())
    )
    if workspace_id is not None:
        stmt = stmt.where(Chat.workspace_id == workspace_id)
    return list((await session.execute(stmt)).scalars())


async def get_chat(session: AsyncSession, ctx: TenantContext, chat_id: UUID) -> Chat:
    chat = (
        await session.execute(
            select(Chat).where(
                Chat.id == chat_id, Chat.org_id == ctx.org_id, Chat.user_id == ctx.user_id
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        raise NotFoundError("chat not found")
    # RBAC-03: a chat's workspace membership can be revoked after the chat
    # was created (offboarding); re-check on every read so historical chat
    # access ends immediately, same posture as documents/workspaces -- never
    # rely on the chat's own existence to imply current access. Mirrors
    # tenancy.service.get_workspace_checked's own membership rule.
    if ctx.role == "user" and chat.workspace_id not in ctx.workspace_ids:
        raise NotFoundError("chat not found")
    return chat


async def rename_chat(
    session: AsyncSession, ctx: TenantContext, chat_id: UUID, title: str
) -> Chat:
    chat = await get_chat(session, ctx, chat_id)
    chat.title = title
    await session.commit()
    return chat


async def delete_chat(session: AsyncSession, ctx: TenantContext, chat_id: UUID) -> None:
    chat = await get_chat(session, ctx, chat_id)
    await session.delete(chat)  # messages + citations cascade at the DB layer
    await session.commit()
