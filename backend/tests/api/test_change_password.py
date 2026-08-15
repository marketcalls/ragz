from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.modules.auth.models import User
from ragz.modules.email import service as email_service


async def _login_headers(client: httpx.AsyncClient, email: str, password: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_change_password_sends_confirmation_email(
    client: httpx.AsyncClient, seeded_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whole-branch review: a successful change sends a best-effort
    'password changed' notification (account-takeover early warning),
    symmetric with reset_password."""
    calls: list[dict[str, Any]] = []

    async def _rec(session: AsyncSession, *, to: str, rendered: tuple[str, str, str],
                   settings: Settings) -> None:
        calls.append({"to": to})

    monkeypatch.setattr(email_service, "send_rendered", _rec)
    headers = await _login_headers(client, seeded_user.email, "pw123456")
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "pw123456", "new_password": "new-password-123"},
        headers=headers,
    )
    assert r.status_code == 204
    assert len(calls) == 1
    assert calls[0]["to"] == seeded_user.email


async def test_change_password_wrong_current_is_401_no_change(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    headers = await _login_headers(client, seeded_user.email, "pw123456")
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "totally-wrong", "new_password": "new-password-123"},
        headers=headers,
    )
    assert r.status_code == 401

    # old password still works
    still_ok = await client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    assert still_ok.status_code == 200


async def test_change_password_correct_current_updates_hash(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    headers = await _login_headers(client, seeded_user.email, "pw123456")
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "pw123456", "new_password": "new-password-123"},
        headers=headers,
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


async def test_change_password_revokes_other_refresh_sessions(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    login = await client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    old_refresh_cookie = login.cookies["refresh_token"]
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "pw123456", "new_password": "new-password-123"},
        headers=headers,
    )
    assert r.status_code == 204

    client.cookies.set("refresh_token", old_refresh_cookie, path="/api/v1/auth")
    refresh = await client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 401


async def test_change_password_unauthenticated_is_401(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "pw123456", "new_password": "new-password-123"},
    )
    assert r.status_code == 401


async def test_change_password_rate_limited(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    headers = await _login_headers(client, seeded_user.email, "pw123456")
    for _ in range(10):
        r = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "wrong", "new_password": "new-password-123"},
            headers=headers,
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-password-123"},
        headers=headers,
    )
    assert r.status_code == 429


async def test_change_password_min_length_validation(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    headers = await _login_headers(client, seeded_user.email, "pw123456")
    r = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "pw123456", "new_password": "short"},
        headers=headers,
    )
    assert r.status_code == 422
