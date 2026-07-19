from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.api.routes.auth import _set_refresh
from raghub.core.config import Settings, get_settings
from raghub.core.errors import AuthenticationError, NotFoundError, UpstreamError
from raghub.core.ratelimit import rate_limit
from raghub.modules.auth import oidc
from raghub.modules.auth import service as auth_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.public_api_base_url}/api/v1/auth/oidc/callback"


@router.get(
    "/status",
    dependencies=[Depends(rate_limit("oidc_status", limit=60, window_seconds=60))],
)
async def status(session: SessionDep) -> dict[str, bool]:
    from raghub.core.app_settings import get_app_setting

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
    url = await oidc.begin_login(
        provider, request.app.state.redis, redirect_uri=_redirect_uri(settings)
    )
    return RedirectResponse(url, status_code=302)


@router.get(
    "/callback",
    dependencies=[Depends(rate_limit("oidc_callback", limit=10, window_seconds=60))],
)
async def callback(
    code: str, state: str, request: Request, session: SessionDep, settings: SettingsDep
) -> RedirectResponse:
    # This is a top-level browser navigation (redirect from the IdP), not an
    # API call -- a problem+json body would render as raw JSON to the user.
    # Any failure here sends the browser back to the login page instead; the
    # reason stays in structlog, never in the redirect URL (contract C7).
    try:
        provider = await oidc.load_provider(session, transport=request.app.state.oidc_transport)
        if provider is None:
            raise NotFoundError("SSO is not configured")
        email = await oidc.complete_login(
            session, provider, request.app.state.redis,
            code=code, state=state, redirect_uri=_redirect_uri(settings),
            settings=settings, transport=request.app.state.oidc_transport,
        )
        pair = await auth_service.login_oidc(session, email=email, settings=settings)
    except (AuthenticationError, UpstreamError, NotFoundError):
        log.warning("oidc_callback_failed", exc_info=True)
        return RedirectResponse(
            f"{settings.frontend_base_url}/login?sso_error=1", status_code=302
        )
    response = RedirectResponse(f"{settings.frontend_base_url}/", status_code=302)
    _set_refresh(response, pair.refresh_token, settings)
    return response
