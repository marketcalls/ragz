"""bot_integrations (superadmin platform+workspace+user bindings) and
bot_conversations (external chat/channel id -> Ragz Chat, per integration).
Platform credentials live in modules/secrets, not here -- see service.py."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk


class BotIntegration(UUIDPk, Base):
    __tablename__ = "bot_integrations"

    platform: Mapped[str]  # "telegram" | "discord" | "slack"
    name: Mapped[str]
    org_id: Mapped[UUID] = mapped_column(index=True)
    workspace_id: Mapped[UUID]
    user_id: Mapped[UUID]
    webhook_id: Mapped[UUID] = mapped_column(unique=True, index=True, default=uuid4)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[UUID]


class BotConversation(UUIDPk, Base):
    """(integration_id, external_chat_id) -> chat_id, get-or-created on each
    inbound message so a Telegram/Slack/Discord thread keeps Ragz context."""

    __tablename__ = "bot_conversations"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_chat_id", name="uq_bot_conversations_chat"),
    )

    integration_id: Mapped[UUID] = mapped_column(
        ForeignKey("bot_integrations.id", ondelete="CASCADE"), index=True
    )
    external_chat_id: Mapped[str] = mapped_column(index=True)
    chat_id: Mapped[UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
