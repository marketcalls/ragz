from collections.abc import Callable, Iterator
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core import net
from ragz.core.config import Settings, get_settings
from ragz.modules.auth.models import User
from ragz.modules.email import service as email_service
from ragz.modules.secrets import service as secrets_service


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class _FakeLoop:
    """sec RAGZ-PUB-11: `PUT /admin/email` now resolves `smtp_host` via
    `core/net.assert_public_host` when `environment` is production/staging --
    fakes `asyncio.get_running_loop().getaddrinfo` so these HTTP-level tests
    don't depend on real DNS for placeholder hostnames like
    `smtp.example.com`. See `tests/core/test_net.py` for the guard's own
    unit tests (which exercise both the blocked and allowed branches
    directly)."""

    def __init__(self, ip: str) -> None:
        self._ip = ip

    async def getaddrinfo(self, host: str, port: object) -> list[tuple[object, ...]]:
        return [(None, None, None, "", (self._ip, 0))]


@pytest.fixture
def resolve_smtp_host_to(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    def _set(ip: str) -> None:
        monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop(ip))

    return _set


@pytest.fixture
def as_environment(
    client: httpx.AsyncClient, test_settings: Settings
) -> Iterator[Callable[[str], None]]:
    """RAGZ-PUB-05 follow-up: lets a single test temporarily swap the
    `get_settings` override on the already-built `client` app to a different
    `environment`, so the production/staging-only SMTP-TLS gate
    (`PUT /admin/email`) can be exercised without standing up a second
    app/engine/client stack. Only `environment` changes -- everything else
    (kek_file, etc.) stays `test_settings`'s, since the Settings fail-closed
    validator itself is covered separately in `tests/core/test_production_config.py`
    and isn't what this fixture is exercising. Restores the original
    `test_settings` override afterward so later tests in the same run aren't
    affected."""
    transport = client._transport  # noqa: SLF001
    assert isinstance(transport, httpx.ASGITransport)
    app = cast(FastAPI, transport.app)

    def _set(environment: str) -> None:
        app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(
            update={"environment": environment}
        )

    yield _set
    app.dependency_overrides[get_settings] = lambda: test_settings


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


_SMTP_PLAINTEXT_PAYLOAD: dict[str, Any] = {
    "provider": "smtp",
    "from_email": "noreply@ragz.example",
    "smtp_host": "smtp.example.com",
    "smtp_use_tls": False,
}


@pytest.mark.parametrize("environment", ["production", "staging"])
async def test_put_rejects_plaintext_smtp_in_public_deployments(
    client: httpx.AsyncClient,
    superadmin_headers: dict[str, str],
    as_environment: Callable[[str], None],
    environment: str,
) -> None:
    as_environment(environment)
    r = await client.put(
        "/api/v1/admin/email", json=_SMTP_PLAINTEXT_PAYLOAD, headers=superadmin_headers
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "smtp_use_tls" in r.json()["detail"]


@pytest.mark.parametrize("environment", ["production", "staging"])
async def test_put_allows_tls_smtp_in_public_deployments(
    client: httpx.AsyncClient,
    superadmin_headers: dict[str, str],
    as_environment: Callable[[str], None],
    resolve_smtp_host_to: Callable[[str], None],
    environment: str,
) -> None:
    as_environment(environment)
    resolve_smtp_host_to("93.184.216.34")  # public IP -- sec RAGZ-PUB-11 guard must allow it
    payload = dict(_SMTP_PLAINTEXT_PAYLOAD, smtp_use_tls=True)
    r = await client.put("/api/v1/admin/email", json=payload, headers=superadmin_headers)
    assert r.status_code == 200
    assert r.json()["smtp_use_tls"] is True


@pytest.mark.parametrize("environment", ["production", "staging"])
async def test_put_rejects_smtp_host_resolving_to_internal_ip_in_public_deployments(
    client: httpx.AsyncClient,
    superadmin_headers: dict[str, str],
    as_environment: Callable[[str], None],
    resolve_smtp_host_to: Callable[[str], None],
    environment: str,
) -> None:
    """sec RAGZ-PUB-11: a superadmin-set SMTP host that resolves to an
    internal/private address (or the cloud metadata IP) must be rejected at
    the write path, in production/staging -- before it's ever saved for a
    later send to dial."""
    as_environment(environment)
    resolve_smtp_host_to("169.254.169.254")  # cloud metadata address
    payload = dict(_SMTP_PLAINTEXT_PAYLOAD, smtp_use_tls=True)
    r = await client.put("/api/v1/admin/email", json=payload, headers=superadmin_headers)
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_put_allows_internal_smtp_host_outside_production_staging(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    """sec RAGZ-PUB-11: the guard is a no-op outside production/staging (no
    DNS mock here at all -- if the guard fired, it would attempt a real
    lookup of `smtp.example.com` and this test would be flaky/networked
    instead of deterministic), so dev/test SMTP config against
    docker-hostname-style targets keeps working unmodified."""
    payload = dict(_SMTP_PLAINTEXT_PAYLOAD, smtp_use_tls=True)
    r = await client.put("/api/v1/admin/email", json=payload, headers=superadmin_headers)
    assert r.status_code == 200


async def test_put_allows_plaintext_smtp_outside_production_staging(
    client: httpx.AsyncClient, superadmin_headers: dict[str, str]
) -> None:
    # `client`'s default settings fixture uses environment="test" -- must stay
    # permissive (dev/test never get this gate) so local dev and CI aren't broken.
    r = await client.put(
        "/api/v1/admin/email", json=_SMTP_PLAINTEXT_PAYLOAD, headers=superadmin_headers
    )
    assert r.status_code == 200
    assert r.json()["smtp_use_tls"] is False


@pytest.mark.parametrize("environment", ["production", "staging"])
async def test_put_allows_ses_provider_regardless_of_smtp_tls_flag(
    client: httpx.AsyncClient,
    superadmin_headers: dict[str, str],
    as_environment: Callable[[str], None],
    environment: str,
) -> None:
    # The gate is specifically about the SMTP provider; a stale/default
    # smtp_use_tls=False alongside provider="ses" is irrelevant (SMTP isn't
    # even in use) and must not be rejected.
    as_environment(environment)
    payload = dict(_SMTP_PLAINTEXT_PAYLOAD, provider="ses")
    r = await client.put("/api/v1/admin/email", json=payload, headers=superadmin_headers)
    assert r.status_code == 200

