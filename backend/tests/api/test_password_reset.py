import re
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.modules.auth.models import PasswordResetToken, User
from ragz.modules.email import service as email_service

_RESET_URL_RE = re.compile(r"token=([\w\-]+)")


class _Recorder:
    """Stand-in for `email_service.send_rendered`: records every call
    (recipient + the (subject, html, text) tuple), no real send."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_error: Exception | None = None

    async def __call__(
        self, session: AsyncSession, *, to: str, rendered: tuple[str, str, str], settings: Settings
    ) -> None:
        self.calls.append({"to": to, "rendered": rendered})
        if self.raise_error is not None:
            raise self.raise_error

    def raw_token(self) -> str:
        _, _, text = self.calls[0]["rendered"]
        match = _RESET_URL_RE.search(text)
        assert match, f"no token in rendered text: {text!r}"
        return match.group(1)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(email_service, "send_rendered", rec)
    return rec


# --- forgot-password: enumeration-safety ------------------------------------


async def test_forgot_password_unknown_and_known_email_get_identical_response(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    r_known = await client.post(
        "/api/v1/auth/forgot-password", json={"email": seeded_user.email}
    )
    r_unknown = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@acme.com"}
    )
    assert r_known.status_code == r_unknown.status_code == 202
    assert r_known.json() == r_unknown.json()


async def test_forgot_password_creates_token_only_for_known_active_user(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, recorder: _Recorder
) -> None:
    await client.post("/api/v1/auth/forgot-password", json={"email": "nobody@acme.com"})
    assert (
        await session.execute(select(PasswordResetToken))
    ).scalars().all() == []

    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    tokens = (
        (await session.execute(select(PasswordResetToken))).scalars().all()
    )
    assert len(tokens) == 1
    assert tokens[0].user_id == seeded_user.id
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["to"] == seeded_user.email


async def test_forgot_password_inactive_user_gets_202_but_no_token(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, recorder: _Recorder
) -> None:
    seeded_user.active = False
    session.add(seeded_user)
    await session.commit()

    r = await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    assert r.status_code == 202
    assert (await session.execute(select(PasswordResetToken))).scalars().all() == []
    assert recorder.calls == []


async def test_forgot_password_invalidates_prior_unused_tokens(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, recorder: _Recorder
) -> None:
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    tokens = (
        (await session.execute(select(PasswordResetToken).order_by(PasswordResetToken.created_at)))
        .scalars()
        .all()
    )
    assert len(tokens) == 2
    assert tokens[0].used_at is not None  # invalidated by the second request
    assert tokens[1].used_at is None


async def test_forgot_password_per_email_throttle_still_202_no_new_send(
    client: httpx.AsyncClient, seeded_user: User, redis_client: Redis, recorder: _Recorder
) -> None:
    # Cap is 5 sends / 15 minutes for one address (RAGZ-PUB-06 mail-bomb guard).
    for _ in range(5):
        r = await client.post(
            "/api/v1/auth/forgot-password", json={"email": seeded_user.email}
        )
        assert r.status_code == 202
    assert len(recorder.calls) == 5

    r = await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    assert r.status_code == 202  # still identical response
    assert len(recorder.calls) == 5  # no new send past the cap


async def test_forgot_password_send_failure_still_202(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    from ragz.modules.email.errors import EmailError

    recorder.raise_error = EmailError("smtp down")
    r = await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    assert r.status_code == 202


async def test_forgot_password_rate_limited(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    # Per-IP dependency: limit=10/60s. Vary the email so the per-email cap
    # (5/900s, caught inside the service and never surfaced as an error)
    # doesn't mask the IP limiter's 429.
    for i in range(10):
        r = await client.post(
            "/api/v1/auth/forgot-password", json={"email": f"user{i}@acme.com"}
        )
        assert r.status_code == 202
    r = await client.post("/api/v1/auth/forgot-password", json={"email": "one-more@acme.com"})
    assert r.status_code == 429


# --- reset-password ----------------------------------------------------------


async def test_reset_password_sets_new_hash_old_fails_new_works(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()

    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r.status_code == 204

    old = await client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    assert old.status_code == 401

    new = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "new-password-123"},
    )
    assert new.status_code == 200


async def test_reset_password_rejected_for_deactivated_user(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, recorder: _Recorder
) -> None:
    """Whole-branch review (defense-in-depth): a token issued moments before
    the account is deactivated must not reset a now-inactive user."""
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()
    # admin deactivates the account after the token was issued
    seeded_user.active = False
    await session.commit()

    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r.status_code == 401  # generic invalid/expired -- no enumeration


async def test_reset_password_token_is_single_use(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()

    r1 = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r1.status_code == 204

    r2 = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "another-password-456"},
    )
    assert r2.status_code == 401


async def test_reset_password_expired_token_rejected(
    client: httpx.AsyncClient,
    seeded_user: User,
    session: AsyncSession,
    recorder: _Recorder,
) -> None:
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()

    row = (
        await session.execute(select(PasswordResetToken))
    ).scalar_one()
    from datetime import UTC, datetime, timedelta

    row.expires_at = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None)
    session.add(row)
    await session.commit()

    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r.status_code == 401


async def test_reset_password_unknown_token_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "does-not-exist", "new_password": "new-password-123"},
    )
    assert r.status_code == 401


async def test_reset_password_revokes_all_refresh_tokens(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    login = await client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    assert login.status_code == 200
    old_refresh_cookie = login.cookies["refresh_token"]

    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()
    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r.status_code == 204

    # The reset call's own cookie-jar activity may have replaced the refresh
    # cookie; explicitly present the PRE-reset cookie to prove it's dead.
    client.cookies.set("refresh_token", old_refresh_cookie, path="/api/v1/auth")
    refresh = await client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 401


async def test_reset_password_invalidates_old_access_token(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    """sec RAGZ-PUB-06: an access token minted before the reset must be
    rejected on its next use, not survive until its 15-min JWT expiry. A
    fresh login (new password) mints a token that validates normally."""
    login = await client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    assert login.status_code == 200
    stale_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()
    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r.status_code == 204

    stale = await client.get("/api/v1/me/authorization", headers=stale_headers)
    assert stale.status_code == 401

    new = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "new-password-123"},
    )
    fresh_headers = {"Authorization": f"Bearer {new.json()['access_token']}"}
    fresh = await client.get("/api/v1/me/authorization", headers=fresh_headers)
    assert fresh.status_code == 200


async def test_reset_password_sends_changed_email(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    await client.post("/api/v1/auth/forgot-password", json={"email": seeded_user.email})
    token = recorder.raw_token()
    assert len(recorder.calls) == 1  # the reset-link email

    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert r.status_code == 204
    assert len(recorder.calls) == 2  # + the password-changed confirmation
    subject, _html, _text = recorder.calls[1]["rendered"]
    assert "changed" in subject.lower()
    assert recorder.calls[1]["to"] == seeded_user.email


async def test_reset_password_rate_limited(
    client: httpx.AsyncClient, seeded_user: User, recorder: _Recorder
) -> None:
    for _ in range(10):
        r = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "bogus", "new_password": "new-password-123"},
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "bogus", "new_password": "new-password-123"},
    )
    assert r.status_code == 429
