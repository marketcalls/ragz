"""OIDC SSO (AUTH-2): admin config plus the generic authorization-code + PKCE
login flow against any spec-compliant provider (Entra ID, Google Workspace,
Okta, Keycloak, dex).

This module owns the SSO config service functions (get/set the OIDC issuer,
client_id, and client_secret) AND the login flow itself (discovery, PKCE
authorize URL, code exchange, ID token verification). The config write path
composes three sub-writes (two app_settings upserts + one secret write) with
an audit record into a single transaction: every sub-write takes commit=False
and the top-level function commits exactly once, so a mid-flow failure (e.g.
the secret write raising) leaves no partial settings or audit row persisted.

Iron rule 3 note: this module is the SECOND sanctioned caller of
secrets._get_secret_decrypted. It follows the same gateway-boundary pattern
as modules/models/sync.py — the client secret is decrypted in memory for
exactly one outbound token request and never returned, stored, or logged.
The allowlist test in tests/modules/models/test_sync.py names this file.
"""

import base64
import hashlib
import json
import secrets as pysecrets
from dataclasses import dataclass
from uuid import UUID

import httpx
from authlib.jose import JsonWebToken
from authlib.jose.errors import JoseError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting, set_app_setting
from ragz.core.config import Settings
from ragz.core.errors import AuthenticationError, UpstreamError
from ragz.modules.audit.service import record_audit
from ragz.modules.secrets import service as secrets_service

OIDC_ISSUER_KEY = "oidc_issuer"
OIDC_CLIENT_ID_KEY = "oidc_client_id"
OIDC_SECRET_NAME = "oidc:client_secret"  # noqa: S105 - a secret NAME, not a secret

_STATE_TTL_SECONDS = 600
_jwt = JsonWebToken(["RS256"])


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


@dataclass(frozen=True)
class OidcProvider:
    issuer: str
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


async def load_provider(
    session: AsyncSession, *, transport: httpx.AsyncBaseTransport | None = None
) -> OidcProvider | None:
    issuer = await get_app_setting(session, OIDC_ISSUER_KEY)
    client_id = await get_app_setting(session, OIDC_CLIENT_ID_KEY)
    if not issuer or not client_id:
        return None
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamError("OIDC discovery failed") from exc
    doc = resp.json()
    return OidcProvider(
        issuer=str(doc["issuer"]),
        client_id=client_id,
        authorization_endpoint=str(doc["authorization_endpoint"]),
        token_endpoint=str(doc["token_endpoint"]),
        jwks_uri=str(doc["jwks_uri"]),
    )


def _pkce_pair() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def begin_login(provider: OidcProvider, redis: Redis, *, redirect_uri: str) -> str:
    state = pysecrets.token_urlsafe(24)
    nonce = pysecrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    await redis.set(
        f"oidc:state:{state}",
        json.dumps({"verifier": verifier, "nonce": nonce}),
        ex=_STATE_TTL_SECONDS,
    )
    params = httpx.QueryParams({
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{provider.authorization_endpoint}?{params}"


async def complete_login(
    session: AsyncSession,
    provider: OidcProvider,
    redis: Redis,
    *,
    code: str,
    state: str,
    redirect_uri: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Exchange the code, verify the ID token, return the verified email."""
    raw = await redis.getdel(f"oidc:state:{state}")  # single use, atomically
    if raw is None:
        raise AuthenticationError("unknown or expired SSO state")
    stashed = json.loads(raw)
    client_secret = await secrets_service._get_secret_decrypted(  # noqa: SLF001
        session, name=OIDC_SECRET_NAME, settings=settings
    )
    try:
        async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
            token_resp = await client.post(provider.token_endpoint, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider.client_id,
                "client_secret": client_secret,
                "code_verifier": stashed["verifier"],
            })
            token_resp.raise_for_status()
            jwks_resp = await client.get(provider.jwks_uri)
            jwks_resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AuthenticationError("SSO token exchange failed") from exc
    id_token = token_resp.json().get("id_token")
    if not id_token:
        raise AuthenticationError("identity provider returned no ID token")
    try:
        claims = _jwt.decode(
            id_token,
            jwks_resp.json(),
            claims_options={
                "iss": {"essential": True, "value": provider.issuer},
                "aud": {"essential": True, "value": provider.client_id},
                "exp": {"essential": True},
            },
        )
        claims.validate()
    except JoseError as exc:
        raise AuthenticationError("invalid ID token") from exc
    if claims.get("nonce") != stashed["nonce"]:
        raise AuthenticationError("SSO nonce mismatch")
    email = claims.get("email")
    if not email or claims.get("email_verified") is False:
        raise AuthenticationError("identity provider did not supply a verified email")
    return str(email).lower()
