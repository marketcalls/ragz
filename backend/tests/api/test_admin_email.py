from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.modules.auth.models import User
from ragz.modules.email import service as email_service
from ragz.modules.secrets import service as secrets_service


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_get_defaults(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    r = await client.get("/api/v1/admin/email", headers=superadmin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "smtp"
    assert body["from_email"] == ""
    assert body["smtp_password_set"] is False
    assert body["ses_secret_key_set"] is False
    assert "smtp_password" not in body and "ses_secret_key" not in body


async def test_put_round_trips_non_secret_config(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    payload = {
        "provider": "smtp",
        "from_email": "noreply@ragz.example",
        "from_name": "Ragz",
        "smtp_host": "smtp.example.com",
        "smtp_port": 2525,
        "smtp_use_tls": True,
        "smtp_username": "mailer",
        "ses_region": "",
        "ses_access_key_id": "",
    }
    r = await client.put("/api/v1/admin/email", json=payload, headers=superadmin_headers)
    assert r.status_code == 200
    for key, value in payload.items():
        assert r.json()[key] == value
    assert r.json()["smtp_password_set"] is False

    r2 = await client.get("/api/v1/admin/email", headers=superadmin_headers)
    assert r2.status_code == 200
    for key, value in payload.items():
        assert r2.json()[key] == value


async def test_put_writes_secret_never_echoed_and_is_readable(
    client: httpx.AsyncClient,
    superadmin_headers: dict[str, str],
    session: AsyncSession,
    test_settings: Settings,
) -> None:
    r = await client.put(
        "/api/v1/admin/email",
        json={
            "provider": "smtp",
            "from_email": "noreply@ragz.example",
            "smtp_host": "smtp.example.com",
            "smtp_password": "s3cr3t-pw",
        },
        headers=superadmin_headers,
    )
    assert r.status_code == 200
    assert "s3cr3t-pw" not in r.text
    assert r.json()["smtp_password_set"] is True

    r2 = await client.get("/api/v1/admin/email", headers=superadmin_headers)
    assert "s3cr3t-pw" not in r2.text
    assert r2.json()["smtp_password_set"] is True

    # The write actually reached the encrypted store (decrypt via the single
    # sanctioned decrypt path -- same pattern tests/modules/email/test_service.py uses).
    decrypted = await secrets_service._get_secret_decrypted(  # noqa: SLF001
        session, name="smtp_password", settings=test_settings
    )
    assert decrypted == "s3cr3t-pw"  # noqa: S105 - asserting a test fixture value


async def test_put_without_secret_leaves_it_unchanged(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    await client.put(
        "/api/v1/admin/email",
        json={
            "provider": "smtp",
            "from_email": "noreply@ragz.example",
            "smtp_host": "smtp.example.com",
            "smtp_password": "s3cr3t-pw",
        },
        headers=superadmin_headers,
    )
    r = await client.put(
        "/api/v1/admin/email",
        json={
            "provider": "smtp",
            "from_email": "noreply@ragz.example",
            "smtp_host": "smtp.other.example.com",
        },
        headers=superadmin_headers,
    )
    assert r.status_code == 200
    assert r.json()["smtp_host"] == "smtp.other.example.com"
    assert r.json()["smtp_password_set"] is True  # unchanged, still set


async def test_admin_role_is_denied(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")  # role=admin, not superadmin
    assert (await client.get("/api/v1/admin/email", headers=h)).status_code == 403
    r = await client.put(
        "/api/v1/admin/email",
        json={"provider": "smtp", "from_email": "x@x.com", "smtp_host": "h"},
        headers=h,
    )
    assert r.status_code == 403
    r2 = await client.post(
        "/api/v1/admin/email/test", json={"to": "user@example.com"}, headers=h
    )
    assert r2.status_code == 403


async def test_send_test_email_invokes_sender_with_test_template(
    client: httpx.AsyncClient,
    superadmin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_send_rendered(
        session: AsyncSession, *, to: str, rendered: tuple[str, str, str], settings: Settings
    ) -> None:
        calls.append({"to": to, "rendered": rendered})

    monkeypatch.setattr(email_service, "send_rendered", fake_send_rendered)

    r = await client.post(
        "/api/v1/admin/email/test", json={"to": "ops@example.com"}, headers=superadmin_headers
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["to"] == "ops@example.com"
    subject, html, text = calls[0]["rendered"]
    assert subject and html and text
    assert "test" in subject.lower()


async def test_send_test_email_misconfigured_provider_is_problem_json_not_500(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    # No email config/secret written -- default EmailConfig is blank -> EmailError.
    r = await client.post(
        "/api/v1/admin/email/test", json={"to": "ops@example.com"}, headers=superadmin_headers
    )
    assert r.status_code == 502
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Email delivery error"

