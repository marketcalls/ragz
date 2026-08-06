from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk


class User(UUIDPk, Base):
    __tablename__ = "users"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    role: Mapped[str]  # superadmin | admin | user
    active: Mapped[bool] = mapped_column(default=True)
    # Plan H (RBAC-2): custom role template for "user"-tier accounts only.
    custom_role_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("role_templates.id", ondelete="SET NULL"), default=None
    )


class RefreshToken(UUIDPk, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)


class Invitation(UUIDPk, Base):
    __tablename__ = "invitations"

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(default="user")
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None] = mapped_column(default=None)


class ApiKey(UUIDPk, Base):
    """Superadmin-controlled external-API key, bound to a (user, workspace)
    pair (iron rule 3: only prefix + peppered hash are stored; see
    auth.api_keys_service for generate/resolve)."""

    __tablename__ = "api_keys"

    prefix: Mapped[str] = mapped_column(index=True)
    key_hash: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str]
    org_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    created_by: Mapped[UUID]
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
