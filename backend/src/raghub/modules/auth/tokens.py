from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from raghub.core.errors import AuthenticationError

_ALG = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    org_id: UUID
    role: str


def issue_access_token(
    *, user_id: UUID, org_id: UUID, role: str, signing_key: str, ttl_seconds: int
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, signing_key, algorithm=_ALG)


def decode_access_token(token: str, signing_key: str) -> AccessClaims:
    try:
        payload = jwt.decode(token, signing_key, algorithms=[_ALG])
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("invalid or expired token") from exc
    return AccessClaims(
        user_id=UUID(payload["sub"]), org_id=UUID(payload["org"]), role=payload["role"]
    )
