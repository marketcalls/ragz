from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from ragz.core.errors import AuthenticationError

_ALG = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    org_id: UUID
    role: str
    # sec RAGZ-PUB-06: the user's security_version at issue time. Compared
    # against the CURRENT column value in tenancy.context.get_tenant_context;
    # a mismatch (including a legacy token minted before this claim existed,
    # which fails the `payload["sv"]` lookup below) is rejected -- fail
    # closed, never treated as "no check requested".
    sv: int


def issue_access_token(
    *,
    user_id: UUID,
    org_id: UUID,
    role: str,
    security_version: int,
    signing_key: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "sv": security_version,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, signing_key, algorithm=_ALG)


def decode_access_token(token: str, signing_key: str) -> AccessClaims:
    try:
        payload = jwt.decode(token, signing_key, algorithms=[_ALG])
        return AccessClaims(
            user_id=UUID(payload["sub"]),
            org_id=UUID(payload["org"]),
            role=payload["role"],
            # sec RAGZ-PUB-06: missing `sv` (a pre-this-change token) raises
            # KeyError here, caught below and turned into a rejection -- fail
            # closed rather than treating an absent claim as "skip the check".
            sv=int(payload["sv"]),
        )
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("invalid or expired token") from exc
