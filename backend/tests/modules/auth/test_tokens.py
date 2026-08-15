from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_or_create_signing_key
from ragz.core.config import Settings
from ragz.core.errors import AuthenticationError
from ragz.modules.auth.tokens import decode_access_token, issue_access_token


async def test_signing_key_persisted(
    session: AsyncSession, test_settings: Settings
) -> None:
    k1 = await get_or_create_signing_key(session, test_settings)
    k2 = await get_or_create_signing_key(session, test_settings)
    assert k1 == k2 and len(k1) >= 43  # 32 bytes urlsafe


def test_token_roundtrip() -> None:
    uid, oid = uuid4(), uuid4()
    tok = issue_access_token(
        user_id=uid, org_id=oid, role="admin", security_version=0,
        signing_key="k" * 43, ttl_seconds=900,
    )
    claims = decode_access_token(tok, "k" * 43)
    assert claims.user_id == uid and claims.org_id == oid and claims.role == "admin"
    assert claims.sv == 0


def test_token_roundtrip_carries_nonzero_security_version() -> None:
    """sec RAGZ-PUB-06: decode_access_token round-trips `sv` -- required for
    tenancy.context.get_tenant_context to compare it against the user's
    current security_version."""
    uid, oid = uuid4(), uuid4()
    tok = issue_access_token(
        user_id=uid, org_id=oid, role="user", security_version=3,
        signing_key="k" * 43, ttl_seconds=900,
    )
    claims = decode_access_token(tok, "k" * 43)
    assert claims.sv == 3


def test_bad_signature_rejected() -> None:
    tok = issue_access_token(
        user_id=uuid4(), org_id=uuid4(), role="user", security_version=0,
        signing_key="k" * 43, ttl_seconds=900,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "x" * 43)


def test_expired_rejected() -> None:
    tok = issue_access_token(
        user_id=uuid4(), org_id=uuid4(), role="user", security_version=0,
        signing_key="k" * 43, ttl_seconds=-1,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "k" * 43)


def test_missing_claim_rejected() -> None:
    now = datetime.now(UTC)
    payload = {"sub": str(uuid4()), "iat": now, "exp": now + timedelta(seconds=900)}
    tok = jwt.encode(payload, "k" * 43, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "k" * 43)


def test_missing_sv_claim_rejected() -> None:
    """sec RAGZ-PUB-06: a legacy access token minted before this change (no
    `sv` claim) must fail closed, not be treated as "no check requested"."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()), "org": str(uuid4()), "role": "user",
        "iat": now, "exp": now + timedelta(seconds=900),
    }
    tok = jwt.encode(payload, "k" * 43, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "k" * 43)
