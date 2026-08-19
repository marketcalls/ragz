import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_or_create_signing_key
from ragz.core.config import Settings
from ragz.core.db import naive_utc
from ragz.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitExceeded,
)
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

# Self-service first-run: fixed 64-bit key for the Postgres transaction
# advisory lock that serializes concurrent first-signups. Any stable constant
# works; it only has to be the SAME value on every call so all racing
# register_first_superadmin transactions contend for one lock. Held for the
# duration of the transaction (pg_advisory_xact_lock, auto-released on
# commit/rollback), so two concurrent bootstraps can never both pass the
# "no superadmin exists" check.
_BOOTSTRAP_ADVISORY_LOCK_KEY = 0x7261677A_626F6F74  # "ragzboot"

# Registration is invite-only once a superadmin exists. Identical message for
# every closed-path rejection (existing superadmin found under the lock, or the
# IntegrityError backstop) so a racing loser can't distinguish the two.
_REGISTRATION_CLOSED = "Registration is closed — ask an administrator for an invitation."


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class SessionInfo:
    """One live refresh-token FAMILY for a user (sec RAGZ-PUB-06 session
    inventory). `created_at` is the earliest token in the family (when the
    session began, at login or the last full re-auth); `last_used_at` is the
    latest token's `created_at` (the most recent rotation -- RefreshToken has
    no separate last-used column, so the newest token's mint time is the best
    available proxy for "last used"); `expires_at` is the live token's
    expiry. `current`-ness is a route-level concern (it depends on which
    refresh cookie the caller presented) and is layered on by the API route,
    not computed here."""

    family_id: UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime


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
        security_version=user.security_version,
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


async def issue_session(
    session: AsyncSession, user: User, settings: Settings
) -> TokenPair:
    """Mint a fresh access+refresh pair for `user`, starting a brand-new
    session family -- the exact token issuance `login` performs. Reused by the
    self-service register route so its auto-login yields the identical pair
    (and refresh cookie) a `/login` would."""
    return await _issue_pair(session, user, uuid4(), settings)


async def needs_bootstrap(session: AsyncSession) -> bool:
    """True when the platform has NO superadmin yet -- i.e. a fresh install
    whose self-service first-run (register_first_superadmin) is still open.
    Once any superadmin exists this returns False and registration is closed."""
    existing = (
        await session.execute(select(User.id).where(User.role == "superadmin").limit(1))
    ).scalar_one_or_none()
    return existing is None


async def register_first_superadmin(
    session: AsyncSession, *, email: str, password: str
) -> User:
    """Self-service first-run: create the platform superadmin, but ONLY on a
    system with zero superadmins. Fail-closed and race-safe:

    - Fail-closed: if any superadmin already exists, raise ConflictError
      ("registration closed"). This path can NEVER mint a second superadmin.
    - Race-safe: a Postgres transaction advisory lock
      (pg_advisory_xact_lock) is taken FIRST, so two concurrent first-signups
      serialize -- the loser blocks until the winner commits, then sees the
      winner's superadmin and is rejected. The lock is xact-scoped
      (auto-released on commit/rollback). The IntegrityError backstop (mirrors
      bootstrap.py) is the last line of defense (e.g. a unique-email/role
      clash from the env bootstrap running concurrently); it also rolls back
      and reports the same closed message.

    The check + create + commit all happen inside this one locked transaction.
    """
    # Serialize concurrent first-signups before reading superadmin state.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _BOOTSTRAP_ADVISORY_LOCK_KEY},
    )
    existing = (
        await session.execute(select(User.id).where(User.role == "superadmin").limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(_REGISTRATION_CLOSED)
    org = Organization(name="Platform")
    session.add(org)
    try:
        await session.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password(password),
            role="superadmin",
        )
        session.add(user)
        await session.flush()
        await record_audit(
            session, org_id=org.id, actor_id=user.id, action="bootstrap.register",
            target_type="user", target_id=str(user.id),
        )
        await session.commit()
    except IntegrityError as exc:
        # Backstop for anything the advisory lock didn't already serialize
        # (e.g. the env-based bootstrap racing in): roll back and report the
        # same closed message rather than surfacing a DB error.
        await session.rollback()
        raise ConflictError(_REGISTRATION_CLOSED) from exc
    return user


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


async def resolve_session_family(
    session: AsyncSession, *, raw_refresh: str, settings: Settings
) -> UUID | None:
    """sec RAGZ-PUB-06: resolves the family_id the caller's OWN refresh_token
    cookie belongs to, for marking the "current" session in list_sessions and
    as the `keep_family_id` for revoke_other_sessions. Looked up by hash
    regardless of revoked/expired status -- the family_id is stable across
    rotation (rotate_refresh keeps it constant), so even a since-rotated
    cookie still identifies the right family; returns None for an unknown
    hash (missing/garbage cookie) rather than raising, since callers treat
    "no current session" as a normal, gracefully-handled case."""
    row = (
        await session.execute(
            select(RefreshToken.family_id).where(
                RefreshToken.token_hash == _hash(raw_refresh, settings.api_key_pepper)
            )
        )
    ).scalar_one_or_none()
    return row


async def list_sessions(session: AsyncSession, user_id: UUID) -> list[SessionInfo]:
    """sec RAGZ-PUB-06: session inventory. A "session" is a refresh-token
    FAMILY (rotate_refresh keeps `family_id` constant across rotations); a
    LIVE session is a family with at least one non-revoked, non-expired
    token. Aggregated in Python rather than SQL GROUP BY -- the per-user
    token count is small (one row per still-tracked rotation across a
    handful of devices), and MIN/MAX-per-family plus a "has a live row"
    filter is simpler to read than the equivalent correlated-subquery SQL.
    """
    now = naive_utc()
    rows = list(
        (
            await session.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id)
            )
        ).scalars()
    )
    families: dict[UUID, list[RefreshToken]] = {}
    for row in rows:
        families.setdefault(row.family_id, []).append(row)

    sessions: list[SessionInfo] = []
    for family_id, tokens in families.items():
        live = [t for t in tokens if t.revoked_at is None and t.expires_at > now]
        if not live:
            continue
        sessions.append(
            SessionInfo(
                family_id=family_id,
                created_at=min(t.created_at for t in tokens),
                last_used_at=max(t.created_at for t in tokens),
                expires_at=max(t.expires_at for t in live),
            )
        )
    sessions.sort(key=lambda s: s.last_used_at, reverse=True)
    return sessions


async def revoke_session(session: AsyncSession, user_id: UUID, family_id: UUID) -> None:
    """Revoke every token in `family_id` -- but ONLY if that family belongs
    to `user_id`. The existence check and the update both scope on
    `user_id`, so a family belonging to another user raises the exact same
    NotFoundError as a family_id that doesn't exist at all (non-leaking:
    the caller can't distinguish "not yours" from "never existed")."""
    owned = (
        await session.execute(
            select(RefreshToken.id)
            .where(RefreshToken.family_id == family_id, RefreshToken.user_id == user_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if owned is None:
        raise NotFoundError("session not found")
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=naive_utc())
    )
    await session.commit()


async def revoke_other_sessions(
    session: AsyncSession, user_id: UUID, keep_family_id: UUID | None
) -> int:
    """Revoke every live family for `user_id` EXCEPT `keep_family_id`. When
    the caller has no identifiable current session (`keep_family_id` is
    None -- e.g. no refresh cookie presented), there is nothing to spare:
    every live family is revoked."""
    stmt = update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if keep_family_id is not None:
        stmt = stmt.where(RefreshToken.family_id != keep_family_id)
    result = cast(
        "CursorResult[Any]", await session.execute(stmt.values(revoked_at=naive_utc()))
    )
    await session.commit()
    return result.rowcount or 0


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
    except EmailError as exc:
        # Best-effort: a provider outage must never surface to the caller
        # (enum-safety) nor block the token from having been issued. Log the
        # error MESSAGE only (no exc_info) so a raw reset token that lives in
        # this scope can never reach the logs via a traceback frame (Rule 3).
        structlog.get_logger().error("password_reset_email_failed", error=str(exc))


async def reset_password(
    session: AsyncSession, *, raw_token: str, new_password: str, settings: Settings
) -> None:
    token = (
        await session.execute(
            select(PasswordResetToken)
            .where(
                PasswordResetToken.token_hash == _hash(raw_token, settings.api_key_pepper)
            )
            # RAGZ-PUB-06 review: lock the row so single-use is atomic. Without
            # this, two concurrent requests carrying the same raw token can both
            # read used_at IS NULL and both succeed. Mirrors rotate_refresh's
            # with_for_update() on the refresh token.
            .with_for_update()
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
    if not user.active:
        # Defense-in-depth: a token issued moments before the account was
        # deactivated must not reset a now-inactive user (mirrors the
        # issuance-time active guard). Generic rejection, no enumeration.
        raise AuthenticationError("invalid or expired reset token")
    user.password_hash = hash_password(new_password)
    token.used_at = now.replace(tzinfo=None)
    # sec RAGZ-PUB-06: bump so every access token minted before this reset --
    # still live for up to 15 more minutes otherwise -- stops validating on
    # its next request (tenancy.context.get_tenant_context's sv check).
    user.security_version += 1
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
    except EmailError as exc:
        # No exc_info: new_password lives in this frame (Rule 3), symmetric
        # with request_password_reset's send-failure log.
        structlog.get_logger().error("password_changed_email_failed", error=str(exc))


async def change_password(
    session: AsyncSession, ctx: TenantContext, *,
    current_password: str, new_password: str, settings: Settings,
) -> None:
    """Authenticated self-service password change (task 8). Revokes ALL
    refresh-token families rather than attempting to spare the calling
    session -- see `_revoke_all_refresh_tokens` for why "other sessions only"
    isn't reliably identifiable from a bearer `TenantContext`. Sends a
    best-effort "password changed" notification (account-takeover early
    warning), symmetric with `reset_password`."""
    user = (await session.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
    if not verify_password(user.password_hash, current_password):
        raise AuthenticationError("current password is incorrect")
    user.password_hash = hash_password(new_password)
    # sec RAGZ-PUB-06: bump so every access token minted before this change --
    # still live for up to 15 more minutes otherwise -- stops validating on
    # its next request (tenancy.context.get_tenant_context's sv check).
    user.security_version += 1
    await _revoke_all_refresh_tokens(session, user.id)
    await record_audit(
        session, org_id=user.org_id, actor_id=user.id, action="password.changed",
        target_type="user", target_id=str(user.id),
    )
    await session.commit()

    try:
        await email_service.send_rendered(
            session, to=user.email, rendered=email_templates.password_changed_email(),
            settings=settings,
        )
    except EmailError as exc:
        # Best-effort notification -- a provider outage must not fail the
        # already-committed change. No exc_info: new_password lives in this
        # frame and must never reach a traceback (Rule 3).
        structlog.get_logger().error("password_changed_email_failed", error=str(exc))


async def create_invitation(
    session: AsyncSession, ctx: TenantContext, *, email: str, role: str,
    settings: Settings, ttl_hours: int = 72, org_id: UUID | None = None,
) -> str:
    """Multi-org completion: `org_id` lets a superadmin invite into ANY
    organization, not just their own. `None` (or the caller's own org)
    preserves the original behavior. Targeting a DIFFERENT org requires
    ctx.role == "superadmin" -- a plain org admin remains hard-scoped to
    ctx.org_id, exactly as before."""
    if org_id is None or org_id == ctx.org_id:
        target_org_id = ctx.org_id
    else:
        if ctx.role != "superadmin":
            raise AuthorizationError("only a superadmin may invite into another organization")
        org = (
            await session.execute(select(Organization).where(Organization.id == org_id))
        ).scalar_one_or_none()
        if org is None:
            raise NotFoundError("organization not found")
        target_org_id = org_id

    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("email already registered")
    raw = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(hours=ttl_hours)).replace(tzinfo=None)
    invitation = Invitation(
        org_id=target_org_id, email=email, role=role,
        token_hash=_hash(raw, settings.api_key_pepper),
        expires_at=expires_at,
    )
    session.add(invitation)
    await session.flush()
    await record_audit(session, org_id=target_org_id, actor_id=ctx.user_id,
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


async def get_custom_role_id(session: AsyncSession, user_id: UUID) -> UUID | None:
    """The user's custom role template id, or None if they have no custom role.

    Exists so entrypoints stop reaching into auth's ORM for one column
    (Phase 2 item 1): /me/authorization was doing `select(User)` in the route
    just to read custom_role_id off the row. Returns the id rather than the
    User so nothing outside this module ends up holding a live user row.
    """
    return (
        await session.execute(select(User.custom_role_id).where(User.id == user_id))
    ).scalar_one_or_none()


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

    Exactly four outcomes:
    - (issuer, subject) already bound to a user: that user logs straight in.
    - No user bound to this identity yet, but the asserted email belongs to
      an existing user already bound to a DIFFERENT (issuer, subject):
      reject. An IdP asserting an email already owned by another bound
      identity must never silently take over the account (mismatch).
    - No user bound to this identity yet, and the asserted email belongs to
      an existing user NOT bound to any (issuer, subject) -- a password
      account, or an OIDC account created before this binding existed:
      reject. This is the residual RAGZ-PUB-02 gap: auto-linking a fresh IdP
      identity onto a pre-existing local account purely because the IdP
      asserts a matching email is an UNAUTHENTICATED linking operation -- an
      over-trusted or misconfigured IdP (or one an attacker can coerce into
      asserting a verified victim email) could otherwise seize any existing
      password account. There is no proof here that the human behind the IdP
      session is the same human who owns the local account. Explicit,
      authenticated linking of an existing account to SSO (e.g. an
      admin-driven "link SSO identity" action, or the logged-in account owner
      confirming the link) is a future feature -- this function only ever
      creates a NEW account or logs into an ALREADY-BOUND one.
    - No user at all: JIT-provision as before (role 'user', domain
      allowlist), persisting the (issuer, subject) binding on the new row.
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
        if existing is not None:
            # Covers both the issuer/subject-mismatch case (already bound to
            # a different identity) and the unbound-existing-account case
            # (see docstring above) -- either way this identity must not be
            # attached to `existing`, and the rejection is identical and
            # generic in both: the browser-facing callback (api/routes/
            # oidc.py) turns any AuthenticationError into the same graceful
            # sso_error redirect, so this never distinguishes "wrong IdP
            # identity" from "email already registered" to the caller. The
            # real reason is server-side only, via the audit action + log.
            await record_audit(session, org_id=existing.org_id, actor_id=None,
                               action="login.oidc_denied", target_type="user",
                               target_id=email)
            await session.commit()
            raise AuthenticationError("cannot sign in with this identity provider")

        domain = email.rsplit("@", 1)[-1]
        orgs = (
            await session.execute(
                select(Organization).where(Organization.sso_domains.contains([domain]))
            )
        ).scalars().all()
        # Zero matches: no org claims the domain. More than one: the unique-
        # claim invariant enforced at write time (tenancy.set_org_sso_domains)
        # was somehow violated anyway (legacy rows, direct DB edits) -- fail
        # loudly rather than silently picking one. Same generic message and
        # denial audit action in every case: the detail must never reveal
        # whether zero or multiple orgs matched (no org enumeration).
        if len(orgs) != 1:
            await record_audit(session, org_id=None, actor_id=None,
                               action="login.oidc_denied", target_type="user",
                               target_id=email)
            await session.commit()
            raise AuthenticationError("no organization accepts this email domain")
        org = orgs[0]

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
