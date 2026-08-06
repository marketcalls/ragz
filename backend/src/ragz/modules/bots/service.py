"""bot_integrations + bot_conversations (iron rule 3: platform credentials
live encrypted in modules/secrets, never plaintext). resolve_by_webhook is
the ONLY lookup path webhook routes use to turn an inbound {platform,
webhook_id} pair into a row -- it 404s on unknown/wrong-platform/disabled
ids alike, so probing random UUIDs learns nothing about which are real.

Iron rule 3 note: get_token/get_signing_secret call
secrets.service._get_secret_decrypted, the single decryption path in the
codebase -- this file is in that function's sanctioned-caller allowlist
(tests/modules/models/test_sync.py)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import ConflictError, NotFoundError
from ragz.modules.bots.models import BotConversation, BotIntegration
from ragz.modules.secrets import service as secrets_service
from ragz.modules.tenancy.models import Workspace, WorkspaceMember


def _token_secret_name(integration_id: UUID) -> str:
    return f"bot:{integration_id}:token"


def _signing_secret_name(integration_id: UUID) -> str:
    return f"bot:{integration_id}:signing"


async def create_integration(
    session: AsyncSession, settings: Settings, *, actor_id: UUID, platform: str, name: str,
    workspace_id: UUID, user_id: UUID, token: str, signing_secret: str,
) -> BotIntegration:
    ws = (
        await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise ConflictError("workspace not found")
    member = (
        await session.execute(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise ConflictError("user is not a member of that workspace")
    row = BotIntegration(
        platform=platform, name=name, org_id=ws.org_id, workspace_id=workspace_id,
        user_id=user_id, created_by=actor_id,
    )
    session.add(row)
    await session.flush()  # need row.id for the secret names below
    await secrets_service.set_secret(
        session, actor_id=actor_id, name=_token_secret_name(row.id), value=token,
        settings=settings, commit=False,
    )
    await secrets_service.set_secret(
        session, actor_id=actor_id, name=_signing_secret_name(row.id), value=signing_secret,
        settings=settings, commit=False,
    )
    await session.commit()
    return row


async def list_integrations(session: AsyncSession) -> list[BotIntegration]:
    return list(
        (
            await session.execute(select(BotIntegration).order_by(BotIntegration.created_at.desc()))
        ).scalars()
    )


async def get_integration(session: AsyncSession, *, integration_id: UUID) -> BotIntegration:
    row = await session.get(BotIntegration, integration_id)
    if row is None:
        raise NotFoundError("bot integration not found")
    return row


async def set_enabled(
    session: AsyncSession, *, integration_id: UUID, enabled: bool
) -> BotIntegration:
    row = await get_integration(session, integration_id=integration_id)
    row.enabled = enabled
    await session.commit()
    return row


async def delete_integration(
    session: AsyncSession, settings: Settings, *, actor_id: UUID, integration_id: UUID
) -> None:
    row = await get_integration(session, integration_id=integration_id)
    for secret_name in (_token_secret_name(row.id), _signing_secret_name(row.id)):
        try:
            await secrets_service.delete_secret(
                session, actor_id=actor_id, name=secret_name, commit=False
            )
        except NotFoundError:
            pass  # a partially-created integration may be missing one secret
    await session.delete(row)
    await session.commit()


async def resolve_by_webhook(
    session: AsyncSession, *, platform: str, webhook_id: UUID
) -> BotIntegration:
    row = (
        await session.execute(
            select(BotIntegration).where(
                BotIntegration.webhook_id == webhook_id,
                BotIntegration.platform == platform,
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.enabled:
        raise NotFoundError("bot integration not found")
    return row


async def get_token(session: AsyncSession, settings: Settings, *, integration_id: UUID) -> str:
    return await secrets_service._get_secret_decrypted(  # noqa: SLF001 -- sanctioned caller
        session, name=_token_secret_name(integration_id), settings=settings
    )


async def get_signing_secret(
    session: AsyncSession, settings: Settings, *, integration_id: UUID
) -> str:
    return await secrets_service._get_secret_decrypted(  # noqa: SLF001 -- sanctioned caller
        session, name=_signing_secret_name(integration_id), settings=settings
    )


async def get_mapped_chat_id(
    session: AsyncSession, *, integration_id: UUID, external_chat_id: str
) -> UUID | None:
    return (
        await session.execute(
            select(BotConversation.chat_id).where(
                BotConversation.integration_id == integration_id,
                BotConversation.external_chat_id == external_chat_id,
            )
        )
    ).scalar_one_or_none()


async def save_chat_mapping(
    session: AsyncSession, *, integration_id: UUID, external_chat_id: str, chat_id: UUID
) -> None:
    session.add(
        BotConversation(
            integration_id=integration_id, external_chat_id=external_chat_id, chat_id=chat_id,
        )
    )
    await session.commit()
