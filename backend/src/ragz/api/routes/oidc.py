from typing import Annotated

import structlog
from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.api.routes.auth import _set_refresh
from ragz.core.config import Settings, get_settings
from ragz.core.errors import (
    AuthenticationError,
    NotFoundError,
    RateLimitExceeded,
    SecretsError,
    UpstreamError,
)
from ragz.core.ratelimit import rate_limit
from ragz.modules.auth import oidc
from ragz.modules.auth import service as auth_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.public_api_base_url}/api/v1/auth/oidc/callback"


# sec RAGZ-PUB-02: scoped to the callback path only -- the browser has no
# other reason to send it, and it never leaves this route pair.
_PREAUTH_COOKIE_PATH = "/api/v1/auth/oidc/callback"


def _set_preauth_cookie(response: RedirectResponse, token: str, settings: Settings) -> None:
    # SameSite=Lax (not Strict): this cookie must survive the top-level
    # navigation the IdP performs back to our callback -- Strict would drop
    # it on that cross-site redirect and break every login. Secure follows
    # the same environment rule as the refresh cookie (api/routes/auth.py
    # _set_refresh): unconditional outside the "test" environment, where the
    # ASGI test client talks plain http.
    response.set_cookie(
        oidc.PREAUTH_COOKIE_NAME, token, httponly=True, samesite="lax",
        secure=settings.environment != "test",
        max_age=oidc.PREAUTH_COOKIE_TTL_SECONDS, path=_PREAUTH_COOKIE_PATH,
    )


def _clear_preauth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(oidc.PREAUTH_COOKIE_NAME, path=_PREAUTH_COOKIE_PATH)


@router.get(
    "/status",
    dependencies=[Depends(rate_limit("oidc_status", limit=60, window_seconds=60))],
)
async def status(session: SessionDep) -> dict[str, bool]:
    from ragz.core.app_settings import get_app_setting

    issuer = await get_app_setting(session, oidc.OIDC_ISSUER_KEY)
    client_id = await get_app_setting(session, oidc.OIDC_CLIENT_ID_KEY)
    return {"enabled": bool(issuer and client_id)}


@router.get(
    "/login",
    dependencies=[Depends(rate_limit("oidc_login", limit=10, window_seconds=60))],
)
async def login(request: Request, session: SessionDep, settings: SettingsDep) -> RedirectResponse:
    provider = await oidc.load_provider(session, transport=request.app.state.oidc_transport)
    if provider is None:
        raise NotFoundError("SSO is not configured")
    txn = await oidc.begin_login(
        provider, request.app.state.redis, redirect_uri=_redirect_uri(settings)
    )
    response = RedirectResponse(txn.authorize_url, status_code=302)
    _set_preauth_cookie(response, txn.preauth_token, settings)
    return response


@router.get("/callback")
async def callback(
    code: str, state: str, request: Request, session: SessionDep, settings: SettingsDep,
    oidc_preauth: Annotated[str | None, Cookie(alias=oidc.PREAUTH_COOKIE_NAME)] = None,
) -> RedirectResponse:
    # This is a top-level browser navigation (redirect from the IdP), not an
    # API call -- a problem+json body would render as raw JSON to the user.
    # Any failure here sends the browser back to the login page instead; the
    # reason stays in structlog, never in the redirect URL (contract C7).
    #
    # The rate-limit guard is deliberately NOT a `Depends(...)` here (unlike
    # every other route in this file): FastAPI runs dependencies before the
    # route body, so a dependency-raised RateLimitExceeded would be dispatched
    # straight to the global problem+json handler, bypassing this try/except
    # entirely and breaking contract C7 for the one failure mode that matters
    # most (a hammered callback URL). Calling the guard as the first statement
    # inside the try block instead keeps it on this route's own error path.
    try:
        await rate_limit("oidc_callback", limit=10, window_seconds=60)(request)
        provider = await oidc.load_provider(session, transport=request.app.state.oidc_transport)
        if provider is None:
            raise NotFoundError("SSO is not configured")
        identity = await oidc.complete_login(
            session, provider, request.app.state.redis,
            code=code, state=state, redirect_uri=_redirect_uri(settings),
            settings=settings, preauth_cookie=oidc_preauth,
            transport=request.app.state.oidc_transport,
        )
        pair = await auth_service.login_oidc(
            session, email=identity.email, issuer=identity.issuer,
            subject=identity.subject, settings=settings,
        )
    except (AuthenticationError, UpstreamError, NotFoundError, SecretsError, RateLimitExceeded):
        log.warning("oidc_callback_failed", exc_info=True)
        error_response = RedirectResponse(
            f"{settings.frontend_base_url}/login?sso_error=1", status_code=302
        )
        _clear_preauth_cookie(error_response)
        return error_response
    response = RedirectResponse(f"{settings.frontend_base_url}/", status_code=302)
    _set_refresh(response, pair.refresh_token, settings)
    _clear_preauth_cookie(response)
    return response
