"""OIDC SSO (AUTH-2). Constants shared by admin config routes and the login
flow. The flow itself lands with the /auth/oidc endpoints.

This module also owns the SSO config service functions (get/set the OIDC
issuer, client_id, and client_secret). The write path composes three
sub-writes (two app_settings upserts + one secret write) with an audit
record into a single transaction: every sub-write takes commit=False and
the top-level function commits exactly once, so a mid-flow failure (e.g. the
secret write raising) leaves no partial settings or audit row persisted.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.app_settings import get_app_setting, set_app_setting
from raghub.core.config import Settings
from raghub.modules.audit.service import record_audit
from raghub.modules.secrets import service as secrets_service

OIDC_ISSUER_KEY = "oidc_issuer"
OIDC_CLIENT_ID_KEY = "oidc_client_id"
OIDC_SECRET_NAME = "oidc:client_secret"  # noqa: S105 - a secret NAME, not a secret


@dataclass(frozen=True)
class SsoConfig:
    issuer: str | None
    client_id: str | None
    client_secret_set: bool


async def _secret_set(session: AsyncSession) -> bool:
    return any(s.name == OIDC_SECRET_NAME for s in await secrets_service.list_secrets(session))


async def get_sso_config(session: AsyncSession) -> SsoConfig:
    return SsoConfig(
        issuer=await get_app_setting(session, OIDC_ISSUER_KEY),
        client_id=await get_app_setting(session, OIDC_CLIENT_ID_KEY),
        client_secret_set=await _secret_set(session),
    )


async def set_sso_config(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    issuer: str,
    client_id: str,
    client_secret: str | None,
    settings: Settings,
) -> SsoConfig:
    """Single-transaction composition: settings + optional secret + audit,
    one commit. A failure anywhere (e.g. the secret write raising) rolls the
    whole thing back — no partial issuer/client_id with a missing audit row."""
    await set_app_setting(session, OIDC_ISSUER_KEY, issuer, commit=False)
    await set_app_setting(session, OIDC_CLIENT_ID_KEY, client_id, commit=False)
    if client_secret:
        await secrets_service.set_secret(
            session, actor_id=actor_id, name=OIDC_SECRET_NAME,
            value=client_secret, settings=settings, commit=False,
        )
    await record_audit(
        session, org_id=None, actor_id=actor_id,
        action="sso.config_changed", target_type="sso", target_id="oidc",
    )
    await session.commit()
    return SsoConfig(
        issuer=issuer, client_id=client_id, client_secret_set=await _secret_set(session)
    )
