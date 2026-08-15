import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_or_create_signing_key
from ragz.core.config import Settings
from ragz.core.db import naive_utc
from ragz.core.errors import AuthenticationError, ConflictError, NotFoundError, RateLimitExceeded
from ragz.core.ratelimit import check_rate_limit
from ragz.modules.audit.service import record_audit
from ragz.modules.auth.models import Invitation, PasswordResetToken, RefreshToken, User
from ragz.modules.auth.passwords import hash_password, verify_password
from ragz.modules.auth.tokens import issue_access_token
from ragz.modules.email import service as email_service
from ragz.modules.email import templates as email_templates
from ragz.modules.email.errors import EmailError
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Organization

# RAGZ-PUB-06: per-email throttle on forgot-password SENDS (distinct from the
# per-IP rate_limit("forgot_password") dependency on the route) -- caps how
# many reset emails one address can be mailed in a window so the endpoint
# can't be used to mail-bomb an arbitrary inbox. Checked ONLY for a matching
# active user (see request_password_reset) so it never becomes an
# enumeration side-channel on its own; the response is identical either way.
_FORGOT_EMAIL_MAX_SENDS = 5
_FORGOT_EMAIL_WINDOW_SECONDS = 900  # 15 minutes

# RAGZ-PUB-06: reset TTL for a self-service PasswordResetToken.
_RESET_TOKEN_TTL_MINUTES = 45


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


def _hash(raw: str, pepper: str = "") -> str:
    # Peppered when a server-side pepper is configured (see
    # Settings.api_key_pepper), else bare SHA-256 for backward compatibility
    # with hashes minted before the pepper existed. Both compare in constant
    # time inside hexdigest equality via the DB unique-index lookup.
    if pepper:
        return hmac.new(pepper.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_pair(
    session: AsyncSession, user: User, family_id: UUID, settings: Settings
) -> TokenPair:
    signing_key = await get_or_create_signing_key(session, settings)
    raw_refresh = secrets.token_urlsafe(48)
    ttl = timedelta(seconds=settings.refresh_token_ttl_seconds)
    expires_at = (datetime.now(UTC) + ttl).replace(tzinfo=None)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=_hash(raw_refresh, settings.api_key_pepper),
            expires_at=expires_at,
        )
    )
    await session.commit()
    access = issue_access_token(
        user_id=user.id, org_id=user.org_id, role=user.role,
        signing_key=signing_key, ttl_seconds=settings.access_token_ttl_seconds,
    )
    return TokenPair(access_token=access, refresh_token=raw_refresh)


async def login(
    session: AsyncSession, *, email: str, password: str, settings: Settings
) -> TokenPair:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.active or not verify_password(user.password_hash, password):
        await record_audit(session, org_id=None, actor_id=None, action="login.failure",
                           target_type="user", target_id=email)
        await session.commit()
        raise AuthenticationError("invalid credentials")
    await record_audit(session, org_id=user.org_id, actor_id=user.id, action="login.success",
                       target_type="user", target_id=str(user.id))
    return await _issue_pair(session, user, uuid4(), settings)


async def rotate_refresh(
    session: AsyncSession, *, raw_refresh: str, settings: Settings
) -> TokenPair:
    row = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == _hash(raw_refresh, settings.api_key_pepper))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        structlog.get_logger().info("refresh_rejected", reason="unknown")
        raise AuthenticationError("invalid refresh token")
    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    grace_reissue = False
    if row.revoked_at is not None:
        # Reuse of a revoked token. Two live tabs racing on the same cookie is
        # legitimate: the loser lands here moments after the winner rotated.
        # Treat reuse inside the grace window as a benign concurrent rotation,
        # but ONLY when the family shows evidence of a real rotation — a live
        # successor token. A token revoked by logout has no successor and must
        # never resurrect. Anything else keeps the theft response: revoke the
        # entire family, uniform error.
        within_grace = now - row.revoked_at.replace(tzinfo=UTC) <= timedelta(
            seconds=settings.refresh_reuse_grace_seconds
        )
        has_live_successor = (
            await session.execute(
                select(RefreshToken.id)
                .where(
                    RefreshToken.family_id == row.family_id,
                    RefreshToken.id != row.id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > now_naive,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        if not (within_grace and has_live_successor):
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id)
                .values(revoked_at=now_naive)
            )
            await session.commit()
            structlog.get_logger().info("refresh_rejected", reason="reuse_detected")
            raise AuthenticationError("invalid refresh token")
        grace_reissue = True
    if row.expires_at.replace(tzinfo=UTC) < now:
        # Expiry wins even inside the grace window.
        structlog.get_logger().info("refresh_rejected", reason="expired")
        raise AuthenticationError("invalid refresh token")
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    if not user.active:
        # Token stays untouched: an inactive user can't rotate anyway, and if
        # reactivated the token resumes working within its original expiry.
        structlog.get_logger().info("refresh_rejected", reason="user_inactive")
        raise AuthenticationError("invalid refresh token")
    if grace_reissue:
        # revoked_at stays as-is: the window is anchored to the original
        # rotation and never extends. Serialization: the SELECT ... FOR UPDATE
        # above holds the row lock, so two grace requests on the same token
        # queue up rather than double-issuing unobserved.
        structlog.get_logger().info("refresh_grace_reissue", family_id=str(row.family_id))
    else:
        row.revoked_at = now_naive
    return await _issue_pair(session, user, row.family_id, settings)


async def logout(
    session: AsyncSession, *, raw_refresh: str, settings: Settings
) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw_refresh, settings.api_key_pepper))
        .values(revoked_at=naive_utc())
    )
    await session.commit()


async def _revoke_all_refresh_tokens(session: AsyncSession, user_id: UUID) -> None:
    """Revoke every live refresh-token family for `user_id`, forcing every
    existing session (every device/tab) to re-authenticate. Shared by
    `reset_password` (task 7, unconditionally ALL -- the requester proved
    control of the account via the emailed token, not via an existing
    session) and `change_password` (task 8): the authenticated caller's
    `TenantContext` carries no refresh-token family id (JWTs are bearer
    tokens, not tied 1:1 to the refresh cookie that minted them), so there is
    no reliable way to single out "this" session and spare it. Revoking ALL
    is the simplest secure choice -- see change_password's docstring."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=naive_utc())
    )


async def request_password_reset(
    session: AsyncSession, email: str, settings: Settings, *, redis: Redis | None = None
) -> None:
    """RAGZ-PUB-06: enumeration-safe forgot-password. The RESPONSE the caller
    (the route) sends back is identical no matter what happens in here --
    unknown email, inactive user, per-email send cap, or an email-provider
    failure all fall through to the same silent return. Only a genuine,
    active-user match ever creates a token or sends mail; that asymmetry in
    internal work is accepted (per the plan) as long as it never surfaces in
    the response shape.
    """
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.active:
        return

    if redis is not None:
        email_key = f"rl:forgot_email:{email.strip().lower()}"
        try:
            await check_rate_limit(
                redis, email_key, _FORGOT_EMAIL_MAX_SENDS, _FORGOT_EMAIL_WINDOW_SECONDS
            )
        except RateLimitExceeded:
            # Cap hit for this address: no new token/send, but still return
            # normally -- the 202 response never reveals this happened.
            return

    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    # Invalidate any prior unused tokens so only the newest link works.
    await session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now_naive)
    )
    raw = secrets.token_urlsafe(32)
    expires_at = (now + timedelta(minutes=_RESET_TOKEN_TTL_MINUTES)).replace(tzinfo=None)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash(raw, settings.api_key_pepper),
            expires_at=expires_at,
        )
    )
    await session.commit()

    reset_url = f"{settings.frontend_base_url}/reset-password?token={raw}"
    try:
        await email_service.send_rendered(
            session, to=user.email,
            rendered=email_templates.reset_password_email(
                reset_url, ttl_minutes=_RESET_TOKEN_TTL_MINUTES
            ),
            settings=settings,
        )
    except EmailError:
        # Best-effort: a provider outage must never surface to the caller
        # (enum-safety) nor block the token from having been issued.
        structlog.get_logger().error("password_reset_email_failed", exc_info=True)


async def reset_password(
    session: AsyncSession, *, raw_token: str, new_password: str, settings: Settings
) -> None:
    token = (
        await session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == _hash(raw_token, settings.api_key_pepper)
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        token is None
        or token.used_at is not None
        or token.expires_at.replace(tzinfo=UTC) <= now
    ):
        # Generic message for every rejection reason -- missing, already
        # used, and expired must be indistinguishable to the caller.
        raise AuthenticationError("invalid or expired reset token")

    user = (await session.execute(select(User).where(User.id == token.user_id))).scalar_one()
    user.password_hash = hash_password(new_password)
    token.used_at = now.replace(tzinfo=None)
    await _revoke_all_refresh_tokens(session, user.id)
    await record_audit(
        session, org_id=user.org_id, actor_id=user.id, action="password.reset",
        target_type="user", target_id=str(user.id),
    )
    await session.commit()

    try:
        await email_service.send_rendered(
            session, to=user.email, rendered=email_templates.password_changed_email(),
            settings=settings,
        )
    except EmailError:
        structlog.get_logger().error("password_changed_email_failed", exc_info=True)


async def change_password(
    session: AsyncSession, ctx: TenantContext, *,
    current_password: str, new_password: str, settings: Settings,
) -> None:
    """Authenticated self-service password change (task 8). Revokes ALL
    refresh-token families rather than attempting to spare the calling
    session -- see `_revoke_all_refresh_tokens` for why "other sessions only"
    isn't reliably identifiable from a bearer `TenantContext`. `settings` is
    accepted for interface parity with `reset_password` (no email is sent
    here in this task's scope)."""
    user = (await session.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
    if not verify_password(user.password_hash, current_password):
        raise AuthenticationError("current password is incorrect")
    user.password_hash = hash_password(new_password)
    await _revoke_all_refresh_tokens(session, user.id)
    await record_audit(
        session, org_id=user.org_id, actor_id=user.id, action="password.changed",
        target_type="user", target_id=str(user.id),
    )
    await session.commit()


async def create_invitation(
    session: AsyncSession, ctx: TenantContext, *, email: str, role: str,
    settings: Settings, ttl_hours: int = 72,
) -> str:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("email already registered")
    raw = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(hours=ttl_hours)).replace(tzinfo=None)
    invitation = Invitation(
        org_id=ctx.org_id, email=email, role=role,
        token_hash=_hash(raw, settings.api_key_pepper),
        expires_at=expires_at,
    )
    session.add(invitation)
    await session.flush()
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="invitation.created", target_type="invitation",
                       target_id=str(invitation.id))
    await session.commit()
    return raw


async def accept_invitation(
    session: AsyncSession, *, raw_token: str, password: str, settings: Settings
) -> User:
    inv = (
        await session.execute(
            select(Invitation).where(
                Invitation.token_hash == _hash(raw_token, settings.api_key_pepper)
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if inv is None or inv.accepted_at is not None or inv.expires_at.replace(tzinfo=UTC) < now:
        raise AuthenticationError("invalid or expired invitation")
    inv.accepted_at = now.replace(tzinfo=None)
    user = User(org_id=inv.org_id, email=inv.email,
                password_hash=hash_password(password), role=inv.role)
    session.add(user)
    await session.flush()
    await record_audit(session, org_id=inv.org_id, actor_id=None,
                       action="invitation.accepted", target_type="user",
                       target_id=str(user.id))
    await session.commit()
    return user


async def list_users(session: AsyncSession, ctx: TenantContext) -> list[User]:
    return list(
        (
            await session.execute(
                select(User)
                .where(User.org_id == ctx.org_id, User.role != "superadmin")
                .order_by(User.email)
            )
        ).scalars()
    )


async def _org_user(session: AsyncSession, ctx: TenantContext, user_id: UUID) -> User:
    user = (
        await session.execute(
            select(User).where(User.id == user_id, User.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if user is None or user.role == "superadmin":
        raise NotFoundError("user not found")
    return user


async def get_org_user(session: AsyncSession, ctx: TenantContext, user_id: UUID) -> User:
    return await _org_user(session, ctx, user_id)


async def set_user_active(
    session: AsyncSession, ctx: TenantContext, user_id: UUID, active: bool
) -> User:
    user = await _org_user(session, ctx, user_id)
    user.active = active
    if not active:
        await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                           action="user.deactivated", target_type="user",
                           target_id=str(user_id))
    await session.commit()
    return user


async def set_user_role(
    session: AsyncSession, ctx: TenantContext, user_id: UUID, role: str
) -> User:
    user = await _org_user(session, ctx, user_id)
    user.role = role
    await record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id,
                       action="user.role_changed", target_type="user",
                       target_id=str(user_id))
    await session.commit()
    return user


async def login_oidc(
    session: AsyncSession, *, email: str, issuer: str, subject: str, settings: Settings
) -> TokenPair:
    """Session issuance for an OIDC-verified identity (AUTH-2 + AUTH-6).

    sec RAGZ-PUB-02: users are resolved by the durable (issuer, subject) pair
    FIRST, never by email alone -- email is IdP-controlled and not guaranteed
    exclusive, so a global email match cannot be trusted to pick the account
    to log into.

    - (issuer, subject) already bound to a user: that user logs straight in.
    - No user bound to this identity yet, but a local user already owns this
      email:
        - already bound to a DIFFERENT (issuer, subject): reject. An IdP
          asserting an email already owned by another bound identity must
          never silently take over the account (issuer/subject mismatch).
        - not bound to any (issuer, subject) yet (a password user, or an
          OIDC account created before this binding existed): the binding may
          only be established under the SAME org domain-allowlist policy
          that gates JIT creation below, and only for the org that account
          already belongs to -- otherwise trusting the IdP's email claim to
          attach to an arbitrary existing account is exactly the
          account-takeover this fix closes.
    - No user at all: JIT-provision as before (role 'user', domain
      allowlist), now also persisting the (issuer, subject) binding.
    """
    user = (
        await session.execute(
            select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject)
        )
    ).scalar_one_or_none()

    if user is None:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None and (
            existing.oidc_issuer is not None or existing.oidc_subject is not None
        ):
            await record_audit(session, org_id=existing.org_id, actor_id=None,
                               action="login.oidc_denied", target_type="user",
                               target_id=email)
            await session.commit()
            raise AuthenticationError("identity provider mismatch for this account")

        domain = email.rsplit("@", 1)[-1]
        orgs = (
            await session.execute(
                select(Organization).where(Organization.sso_domains.contains([domain]))
            )
        ).scalars().all()
        # Zero matches: no org claims the domain. More than one: the unique-
        # claim invariant enforced at write time (tenancy.set_org_sso_domains)
        # was somehow violated anyway (legacy rows, direct DB edits) -- fail
        # loudly rather than silently picking one. An existing user's org must
        # also be the ONE org claiming the domain -- otherwise the domain was
        # reassigned since that account was created and binding it now would
        # silently move it across tenants. Same generic message and denial
        # audit action in every case: the detail must never reveal whether
        # zero or multiple orgs matched (no org enumeration), nor whether an
        # existing account was in play.
        if len(orgs) != 1 or (existing is not None and orgs[0].id != existing.org_id):
            await record_audit(session, org_id=None, actor_id=None,
                               action="login.oidc_denied", target_type="user",
                               target_id=email)
            await session.commit()
            raise AuthenticationError("no organization accepts this email domain")
        org = orgs[0]

        if existing is not None:
            # Unbound existing user whose email domain the owning org still
            # claims: safe to establish the (issuer, subject) binding now,
            # under the same allowlist policy JIT creation uses below.
            existing.oidc_issuer = issuer
            existing.oidc_subject = subject
            user = existing
        else:
            user = User(
                org_id=org.id, email=email,
                # unusable password: SSO users authenticate only via the IdP
                password_hash=hash_password(secrets.token_urlsafe(32)),
                role="user", oidc_issuer=issuer, oidc_subject=subject,
            )
            session.add(user)
            await session.flush()
            await record_audit(session, org_id=org.id, actor_id=user.id,
                               action="user.oidc_provisioned", target_type="user",
                               target_id=str(user.id))
    if not user.active:
        raise AuthenticationError("user inactive")
    await record_audit(session, org_id=user.org_id, actor_id=user.id,
                       action="login.oidc", target_type="user", target_id=str(user.id))
    return await _issue_pair(session, user, uuid4(), settings)
