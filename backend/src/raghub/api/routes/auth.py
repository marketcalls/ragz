from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.api.deps import get_session
from raghub.core.config import Settings, get_settings
from raghub.core.errors import AuthenticationError
from raghub.modules.auth import service
from raghub.modules.auth.schemas import (
    AccessTokenResponse,
    InvitationAccept,
    InvitationCreate,
    InvitationOut,
    LoginRequest,
)
from raghub.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RefreshCookie = Annotated[str | None, Cookie(alias="refresh_token")]
AdminDep = Annotated[TenantContext, Depends(require_role("admin"))]


def _set_refresh(response: Response, raw: str, settings: Settings) -> None:
    response.set_cookie(
        "refresh_token", raw, httponly=True, samesite="strict",
        secure=settings.environment != "dev",
        max_age=settings.refresh_token_ttl_seconds, path="/api/v1/auth",
    )


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    body: LoginRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> AccessTokenResponse:
    pair = await service.login(session, email=body.email, password=body.password, settings=settings)
    _set_refresh(response, pair.refresh_token, settings)
    return AccessTokenResponse(access_token=pair.access_token)


@router.post("/refresh", response_model=AccessTokenResponse)
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
    response: Response, session: SessionDep, refresh_token: RefreshCookie = None
) -> None:
    if refresh_token:
        await service.logout(session, raw_refresh=refresh_token)
    response.delete_cookie("refresh_token", path="/api/v1/auth")


@router.post("/invitations", status_code=201, response_model=InvitationOut)
async def create_invitation(
    body: InvitationCreate, session: SessionDep, ctx: AdminDep
) -> InvitationOut:
    raw = await service.create_invitation(session, ctx, email=body.email, role=body.role)
    return InvitationOut(invite_token=raw)


@router.post("/invitations/accept", status_code=201)
async def accept_invitation(body: InvitationAccept, session: SessionDep) -> dict[str, str]:
    user = await service.accept_invitation(session, raw_token=body.token, password=body.password)
    return {"email": user.email}
