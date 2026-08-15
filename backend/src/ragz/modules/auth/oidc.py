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
import hmac
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
from ragz.core.net import assert_public_url
from ragz.modules.audit.service import record_audit
from ragz.modules.secrets import service as secrets_service

OIDC_ISSUER_KEY = "oidc_issuer"
OIDC_CLIENT_ID_KEY = "oidc_client_id"
OIDC_SECRET_NAME = "oidc:client_secret"  # noqa: S105 - a secret NAME, not a secret

_STATE_TTL_SECONDS = 600
_jwt = JsonWebToken(["RS256"])

# sec RAGZ-PUB-02: name of the HttpOnly pre-auth cookie the login route sets
# to bind the OAuth transaction to the browser that started it. Shared with
# api/routes/oidc.py, which owns setting/clearing the cookie itself; this
# module only ever sees the raw token value (to hash it) or the stored hash
# (to compare), never sets/clears cookies directly.
PREAUTH_COOKIE_NAME = "oidc_preauth"  # noqa: S105 - a cookie NAME, not a secret
PREAUTH_COOKIE_TTL_SECONDS = _STATE_TTL_SECONDS


def _hash_preauth_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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
    session: AsyncSession,
    *,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OidcProvider | None:
    issuer = await get_app_setting(session, OIDC_ISSUER_KEY)
    client_id = await get_app_setting(session, OIDC_CLIENT_ID_KEY)
    if not issuer or not client_id:
        return None
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    # sec RAGZ-PUB-11: the issuer is superadmin-set (set_sso_config, above) --
    # a privileged-but-malicious or misconfigured value could otherwise
    # target an internal service or the cloud metadata endpoint. No-op in
    # dev/test; production/staging only.
    await assert_public_url(url, settings)
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


@dataclass(frozen=True)
class LoginTransaction:
    authorize_url: str
    # Raw pre-auth token for the route to set as an HttpOnly cookie. Only its
    # SHA-256 hash is persisted (below) -- this value never touches Redis,
    # logs, or storage; it lives only in the redirect response's Set-Cookie.
    preauth_token: str


async def begin_login(
    provider: OidcProvider, redis: Redis, *, redirect_uri: str
) -> LoginTransaction:
    state = pysecrets.token_urlsafe(24)
    nonce = pysecrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    # sec RAGZ-PUB-02: bind this transaction to the browser that started it.
    # The token itself is handed back to the route (to set as a cookie); only
    # its hash is stored server-side, so a leaked/observed Redis record alone
    # can never satisfy the callback's cookie check.
    preauth_token = pysecrets.token_urlsafe(32)
    await redis.set(
        f"oidc:state:{state}",
        json.dumps({
            "verifier": verifier,
            "nonce": nonce,
            "preauth_hash": _hash_preauth_token(preauth_token),
        }),
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
    return LoginTransaction(
        authorize_url=f"{provider.authorization_endpoint}?{params}",
        preauth_token=preauth_token,
    )


@dataclass(frozen=True)
class OidcIdentity:
    email: str
    issuer: str
    subject: str


async def complete_login(
    session: AsyncSession,
    provider: OidcProvider,
    redis: Redis,
    *,
    code: str,
    state: str,
    redirect_uri: str,
    settings: Settings,
    preauth_cookie: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OidcIdentity:
    """Exchange the code, verify the ID token, return the verified identity."""
    raw = await redis.getdel(f"oidc:state:{state}")  # single use, atomically
    if raw is None:
        raise AuthenticationError("unknown or expired SSO state")
    stashed = json.loads(raw)
    # sec RAGZ-PUB-02: the transaction must be bound to the SAME browser that
    # started it. Without this, an attacker can begin their own login, hand
    # the still-unconsumed callback URL to a victim, and have the victim's
    # browser walk into the attacker's session (login CSRF / session swap).
    # Checked BEFORE the code is exchanged with the IdP -- a mismatch or
    # missing cookie must never reach the token endpoint. Constant-time
    # compare since both sides are attacker-observable-length hex digests.
    if preauth_cookie is None or not hmac.compare_digest(
        _hash_preauth_token(preauth_cookie), stashed.get("preauth_hash", "")
    ):
        raise AuthenticationError("SSO transaction not bound to this browser")
    client_secret = await secrets_service._get_secret_decrypted(  # noqa: SLF001
        session, name=OIDC_SECRET_NAME, settings=settings
    )
    # sec RAGZ-PUB-11: `token_endpoint`/`jwks_uri` come from the discovery
    # document the issuer returned (load_provider, above) -- guarded again
    # here, right before they're dialed, in case the discovery document
    # itself points somewhere the issuer URL didn't. No-op in dev/test.
    await assert_public_url(provider.token_endpoint, settings)
    await assert_public_url(provider.jwks_uri, settings)
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
    # sec RAGZ-PUB-02: require email_verified is True, not merely "not False".
    # A MISSING claim used to be silently accepted -- an IdP that omits the
    # claim (or is coerced into omitting it) must not be trusted as if it had
    # asserted verification.
    if not email or claims.get("email_verified") is not True:
        raise AuthenticationError("identity provider did not supply a verified email")
    subject = claims.get("sub")
    if not subject:
        raise AuthenticationError("identity provider did not supply a subject")
    return OidcIdentity(email=str(email).lower(), issuer=provider.issuer, subject=str(subject))
