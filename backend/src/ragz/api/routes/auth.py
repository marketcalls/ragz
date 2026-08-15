from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import AuthenticationError
from ragz.core.ratelimit import peek_rate_limit, rate_limit, record_failure
from ragz.modules.auth import service
from ragz.modules.auth.schemas import (
    AccessTokenResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    InvitationAccept,
    InvitationCreate,
    InvitationOut,
    LoginRequest,
    ResetPasswordRequest,
    SessionOut,
)
from ragz.modules.tenancy.context import TenantContext, get_tenant_context, require_role

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RefreshCookie = Annotated[str | None, Cookie(alias="refresh_token")]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]
AuthedDep = Annotated[TenantContext, Depends(get_tenant_context)]

# Constant response body for /auth/forgot-password: RAGZ-PUB-06 enum-safety
# requires the exact same 202 body whether or not the email matches an
# active account.
_FORGOT_PASSWORD_RESPONSE = {"detail": "If that email exists, a reset link has been sent."}

# RAGZ-PUB-06: per-ACCOUNT failed-login throttle, orthogonal to the per-IP
# `rate_limit("login")` dependency. A distributed botnet spreading guesses for
# one privileged email across many source IPs slips past the per-IP limiter;
# this caps failed attempts per normalized account identifier. Counts ONLY
# failures (see peek_rate_limit/record_failure) so a legitimate user's success
# never advances the counter, and the window auto-expires. Generous enough not
# to lock out honest mistypes, tight enough to throttle stuffing.
_LOGIN_ACCOUNT_MAX_FAILURES = 10
_LOGIN_ACCOUNT_WINDOW_SECONDS = 900  # 15 minutes


def _set_refresh(response: Response, raw: str, settings: Settings) -> None:
    # RAGZ-PUB-05: Secure is unconditional outside the pytest/httpx test
    # harness -- dev and staging/production all get Secure cookies now. Only
    # environment == "test" relaxes it, since the ASGI test client talks
    # plain http and a Secure cookie would silently vanish from its jar.
    response.set_cookie(
        "refresh_token", raw, httponly=True, samesite="strict",
        secure=settings.environment != "test",
        max_age=settings.refresh_token_ttl_seconds, path="/api/v1/auth",
    )


@router.post(
    "/login",
    response_model=AccessTokenResponse,
    dependencies=[Depends(rate_limit("login", limit=10, window_seconds=60))],
)
async def login(
    body: LoginRequest, request: Request, response: Response,
    session: SessionDep, settings: SettingsDep,
) -> AccessTokenResponse:
    redis = request.app.state.redis
    account_key = f"rl:login_account:{body.email.strip().lower()}"
    # Gate on prior failures for THIS account before spending a credential check.
    await peek_rate_limit(redis, account_key, _LOGIN_ACCOUNT_MAX_FAILURES)
    try:
        pair = await service.login(
            session, email=body.email, password=body.password, settings=settings
        )
    except AuthenticationError:
        # Record the failed attempt against the account, then surface the same
        # generic error (no enumeration: identical response for bad email vs
        # bad password vs inactive user, exactly as service.login already does).
        await record_failure(redis, account_key, _LOGIN_ACCOUNT_WINDOW_SECONDS)
        raise
    # Successful auth clears the account's failure counter (a real user who
    # mistyped a few times isn't left throttled after they get in).
    await redis.delete(account_key)
    _set_refresh(response, pair.refresh_token, settings)
    return AccessTokenResponse(access_token=pair.access_token)


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    dependencies=[Depends(rate_limit("refresh", limit=30, window_seconds=60))],
)
async def refresh(
    response: Response, session: SessionDep, settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> AccessTokenResponse:
    if not refresh_token:
        raise AuthenticationError("missing refresh token")
    pair = await service.rotate_refresh(session, raw_refresh=refresh_token, settings=settings)
    _set_refresh(response, pair.refresh_token, settings)
    return AccessTokenResponse(access_token=pair.access_token)


@router.post("/logout", status_code=204)
async def logout(
    response: Response, session: SessionDep, settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    if refresh_token:
        await service.logout(session, raw_refresh=refresh_token, settings=settings)
    response.delete_cookie("refresh_token", path="/api/v1/auth")


@router.post("/invitations", status_code=201, response_model=InvitationOut)
async def create_invitation(
    body: InvitationCreate, session: SessionDep, ctx: AdminDep, settings: SettingsDep
) -> InvitationOut:
    raw = await service.create_invitation(
        session, ctx, email=body.email, role=body.role, settings=settings
    )
    return InvitationOut(invite_token=raw)


@router.post(
    "/invitations/accept",
    status_code=201,
    dependencies=[Depends(rate_limit("invitation_accept", limit=10, window_seconds=60))],
)
async def accept_invitation(
    body: InvitationAccept, session: SessionDep, settings: SettingsDep
) -> dict[str, str]:
    user = await service.accept_invitation(
        session, raw_token=body.token, password=body.password, settings=settings
    )
    return {"email": user.email}


@router.post(
    "/forgot-password",
    status_code=202,
    dependencies=[Depends(rate_limit("forgot_password", limit=10, window_seconds=60))],
)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> dict[str, str]:
    # RAGZ-PUB-06: enum-safe. service.request_password_reset never raises for
    # an unknown/inactive email or a send failure -- the response below is
    # returned unconditionally, identical in every case.
    redis = request.app.state.redis
    await service.request_password_reset(session, body.email, settings, redis=redis)
    return _FORGOT_PASSWORD_RESPONSE


@router.post(
    "/reset-password",
    status_code=204,
    dependencies=[Depends(rate_limit("reset_password", limit=10, window_seconds=60))],
)
async def reset_password(
    body: ResetPasswordRequest, session: SessionDep, settings: SettingsDep
) -> None:
    await service.reset_password(
        session, raw_token=body.token, new_password=body.new_password, settings=settings
    )


@router.post(
    "/change-password",
    status_code=204,
    dependencies=[Depends(rate_limit("change_password", limit=10, window_seconds=60))],
)
async def change_password(
    body: ChangePasswordRequest, session: SessionDep, settings: SettingsDep, ctx: AuthedDep
) -> None:
    await service.change_password(
        session, ctx,
        current_password=body.current_password,
        new_password=body.new_password,
        settings=settings,
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    session: SessionDep, settings: SettingsDep, ctx: AuthedDep,
    refresh_token: RefreshCookie = None,
) -> list[SessionOut]:
    """sec RAGZ-PUB-06: session inventory -- the caller's own live
    refresh-token families only (scoped to ctx.user_id, never another
    user's). The family resolved from the caller's OWN refresh_token cookie
    (if any) is marked `current: true`; gracefully falls back to no
    "current" session at all when the cookie is missing or unrecognized."""
    current_family = None
    if refresh_token:
        current_family = await service.resolve_session_family(
            session, raw_refresh=refresh_token, settings=settings
        )
    sessions = await service.list_sessions(session, ctx.user_id)
    return [
        SessionOut(
            family_id=s.family_id, created_at=s.created_at,
            last_used_at=s.last_used_at, expires_at=s.expires_at,
            current=(s.family_id == current_family),
        )
        for s in sessions
    ]


@router.delete("/sessions/{family_id}", status_code=204)
async def revoke_session(
    family_id: UUID, session: SessionDep, ctx: AuthedDep,
) -> None:
    """sec RAGZ-PUB-06: revoke one specific session (refresh-token family),
    scoped to the caller -- service.revoke_session raises NotFoundError
    (non-leaking) if `family_id` doesn't belong to ctx.user_id."""
    await service.revoke_session(session, ctx.user_id, family_id)


@router.post("/sessions/revoke-others", status_code=204)
async def revoke_other_sessions(
    session: SessionDep, settings: SettingsDep, ctx: AuthedDep,
    refresh_token: RefreshCookie = None,
) -> None:
    """sec RAGZ-PUB-06: revoke every OTHER live session, sparing the one the
    caller's own refresh_token cookie identifies. No cookie (or an
    unrecognized one) means there is nothing to spare -- every live session
    for this user is revoked."""
    keep_family = None
    if refresh_token:
        keep_family = await service.resolve_session_family(
            session, raw_refresh=refresh_token, settings=settings
        )
    await service.revoke_other_sessions(session, ctx.user_id, keep_family)
