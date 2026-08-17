from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core.db import Base, UUIDPk


class User(UUIDPk, Base):
    __tablename__ = "users"
    # RBAC-11: mirrors migration 0521b696bbe9's ck_users_role so create_all
    # (test/dev schema build) enforces it too, not just a genuinely-migrated
    # Postgres.
    __table_args__ = (
        # Composite-FK target -- see Workspace for the rationale.
        UniqueConstraint("id", "org_id", name="uq_users_id_org_id"),
        CheckConstraint(
            "role IN ('superadmin', 'admin', 'user')", name="ck_users_role"
        ),
        # sec RAGZ-PUB-02: mirrors migration a7c3f9e1b2d4's
        # uq_users_oidc_identity so create_all (test/dev schema build)
        # enforces it too. Both columns NULL for password-only users --
        # Postgres treats NULL as distinct under uniqueness, so any number of
        # NULL/NULL rows coexist.
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    role: Mapped[str]  # superadmin | admin | user
    active: Mapped[bool] = mapped_column(default=True)
    # sec RAGZ-PUB-06: bumped on every password change/reset. Stamped into
    # each access token's `sv` claim at issue time (tokens.issue_access_token)
    # and compared against the CURRENT value on every request
    # (tenancy.context.get_tenant_context) -- a mismatch means the token was
    # minted before the most recent credential change, so it's rejected even
    # though it hasn't hit its 15-min JWT expiry yet. Refresh-token rotation
    # re-reads the current value, so a refresh AFTER a bump re-syncs; only
    # access tokens minted before the bump are killed.
    security_version: Mapped[int] = mapped_column(default=0, server_default="0")
    # Plan H (RBAC-2): custom role template for "user"-tier accounts only.
    custom_role_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("role_templates.id", ondelete="SET NULL"), default=None
    )
    # sec RAGZ-PUB-02: durable IdP identity binding. Set together at OIDC
    # login-time resolution/linking (modules/auth/service.login_oidc); never
    # written from an unauthenticated email match alone. NULL/NULL for every
    # password-only account.
    oidc_issuer: Mapped[str | None] = mapped_column(default=None)
    oidc_subject: Mapped[str | None] = mapped_column(default=None)


class RefreshToken(UUIDPk, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)


class Invitation(UUIDPk, Base):
    __tablename__ = "invitations"
    # RBAC-11: mirrors migration 0521b696bbe9's ck_invitations_role.
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_invitations_role"),
    )

    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    email: Mapped[str] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(default="user")
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None] = mapped_column(default=None)


class PasswordResetToken(UUIDPk, Base):
    """Self-service forgot-password token (RAGZ-PUB-06): single-use, hashed,
    short-TTL. Mirrors Invitation's token_hash/expires_at/accepted_at shape."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)


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
    # sec RAGZ-PUB-13: nullable only for legacy pre-fix rows -- generate_api_key
    # (auth.api_keys_service) always writes a bounded value now (no perpetual
    # keys). resolve_api_key treats a NULL here as created_at + max lifetime,
    # not "never expires".
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
