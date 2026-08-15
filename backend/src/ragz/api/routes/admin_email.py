"""Superadmin email config + send-test routes (Task 5 of the email/
password-reset plan). Mirrors `admin_secrets.py` (write-only secrets) and
`settings.py` (`GET`/`PUT /admin/settings` non-secret provider config).

Guard choice: `require_role()` (no roles -> only the superadmin bypass
passes), NOT `require_action("settings.manage")`. `settings.manage` is
auto-granted to every `role="admin"` user (RBAC-05: `PERMISSIONS -
_AUTOMATIC_CARVE_OUTS`, and `settings.manage` is not a carve-out) --
`require_action` would let a plain admin read/write SMTP/SES credentials and
fire test sends. `require_role()` is the same superadmin-only floor
`admin_secrets.py`/`settings.py` already use for this exact action; the
route still DECLARES `settings.manage` in `ROUTE_POLICY` for cataloging.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.api.deps import get_session
from ragz.core.config import Settings, get_settings
from ragz.core.errors import BadRequestError
from ragz.modules.email import service as email_service
from ragz.modules.email import templates
from ragz.modules.email.schemas import (
    EmailConfig,
    EmailConfigOut,
    EmailConfigUpdate,
    EmailTestRequest,
)
from ragz.modules.models import settings_service
from ragz.modules.secrets import service as secrets_service
from ragz.modules.tenancy.context import TenantContext, require_role

router = APIRouter(prefix="/admin/email", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# require_role() with NO roles: only the superadmin bypass passes -> superadmin-only guard.
SuperadminDep = Annotated[TenantContext, Depends(require_role())]

_SMTP_SECRET = "smtp_password"  # noqa: S105 - a secret NAME, not a secret
_SES_SECRET = "ses_secret_key"  # noqa: S105 - a secret NAME, not a secret
_SECRET_NAMES = [_SMTP_SECRET, _SES_SECRET]


def _reject_plaintext_smtp_in_public_deployments(config: EmailConfig, settings: Settings) -> None:
    """RAGZ-PUB-05 follow-up: `smtp_use_tls=false` means the SMTP session
    (including the authenticated username/password and the message body)
    goes out in the clear. `EmailConfig` is DB-stored, not part of
    `Settings`, so it can't be caught by the Settings fail-closed validator
    (`core/config.py`) -- this is the single write path for the config
    (`PUT /admin/email`), so enforcing it here means it's checked exactly
    once, regardless of which sender (`smtp_sender.py`) later consumes the
    stored config. dev/test stay permissive; production/staging reject."""
    if (
        settings.environment in ("production", "staging")
        and config.provider == "smtp"
        and not config.smtp_use_tls
    ):
        raise BadRequestError(
            "smtp_use_tls=false is not permitted in production/staging -- plaintext SMTP "
            "would send credentials and message bodies unencrypted"
        )


async def _to_out(session: AsyncSession) -> EmailConfigOut:
    config = await settings_service.get_email_config(session)
    present = await secrets_service.existing_secret_names(session, _SECRET_NAMES)
    return EmailConfigOut(
        **config.model_dump(),
        smtp_password_set=_SMTP_SECRET in present,
        ses_secret_key_set=_SES_SECRET in present,
    )


@router.get("", response_model=EmailConfigOut)
async def get_email_config_route(session: SessionDep, ctx: SuperadminDep) -> EmailConfigOut:
    return await _to_out(session)


@router.put("", response_model=EmailConfigOut)
async def put_email_config_route(
    body: EmailConfigUpdate, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep,
) -> EmailConfigOut:
    config = EmailConfig(**body.model_dump(exclude={"smtp_password", "ses_secret_key"}))
    _reject_plaintext_smtp_in_public_deployments(config, settings)
    await settings_service.update_email_config(session, config, commit=False)
    if body.smtp_password is not None:
        await secrets_service.set_secret(
            session, actor_id=ctx.user_id, name=_SMTP_SECRET, value=body.smtp_password,
            settings=settings, commit=False,
        )
    if body.ses_secret_key is not None:
        await secrets_service.set_secret(
            session, actor_id=ctx.user_id, name=_SES_SECRET, value=body.ses_secret_key,
            settings=settings, commit=False,
        )
    await session.commit()
    return await _to_out(session)


@router.post("/test")
async def send_test_email_route(
    body: EmailTestRequest, session: SessionDep, settings: SettingsDep, ctx: SuperadminDep,
) -> dict[str, bool]:
    """Sends `templates.test_email()` to `body.to`. A misconfigured provider
    or a send failure raises `EmailError`, which the app-level `RagzError`
    handler turns into a `502 application/problem+json` response -- never a
    bare 500."""
    await email_service.send_rendered(
        session, to=body.to, rendered=templates.test_email(), settings=settings
    )
    return {"ok": True}
