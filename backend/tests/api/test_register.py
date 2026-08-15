"""Self-service first-run: the FIRST registrant becomes the platform
superadmin (creates the "Platform" org), then registration closes. These
tests exercise the public POST /auth/register + GET /auth/bootstrap-status
path and its fail-closed guarantee (409 once a superadmin exists)."""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Organization

_STRONG_PW = "correct-horse-staple-1"  # >= 12 chars (shared password floor)


async def test_bootstrap_status_true_on_fresh_db(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/auth/bootstrap-status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": True}


async def test_register_creates_first_superadmin_and_auto_logs_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "root@platform.example", "password": _STRONG_PW},
    )
    assert r.status_code == 200
    assert r.json()["access_token"]
    # auto-login: the register response sets the refresh cookie, exactly as login
    assert "refresh_token" in r.cookies

    user = (
        await session.execute(select(User).where(User.email == "root@platform.example"))
    ).scalar_one()
    assert user.role == "superadmin"
    org = (
        await session.execute(select(Organization).where(Organization.id == user.org_id))
    ).scalar_one()
    assert org.name == "Platform"


async def test_registered_superadmin_can_login(client: httpx.AsyncClient) -> None:
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "root@platform.example", "password": _STRONG_PW},
    )
    assert reg.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "root@platform.example", "password": _STRONG_PW},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]


async def test_bootstrap_status_false_after_superadmin_exists(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    r = await client.get("/api/v1/auth/bootstrap-status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": False}


async def test_register_closed_once_superadmin_exists(
    client: httpx.AsyncClient, seeded_superadmin: User, session: AsyncSession
) -> None:
    """Fail-closed: a second register attempt is rejected with 409 and creates
    no additional superadmin."""
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "intruder@platform.example", "password": _STRONG_PW},
    )
    assert r.status_code == 409
    assert "closed" in r.json()["detail"].lower()

    supers = (
        await session.execute(select(User).where(User.role == "superadmin"))
    ).scalars().all()
    assert len(supers) == 1  # only the seeded one; no second superadmin minted


async def test_register_closed_after_a_first_successful_register(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """End-to-end: the very first register succeeds; a second one on the now-
    bootstrapped system is closed."""
    first = await client.post(
        "/api/v1/auth/register",
        json={"email": "root@platform.example", "password": _STRONG_PW},
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/register",
        json={"email": "second@platform.example", "password": _STRONG_PW},
    )
    assert second.status_code == 409

    supers = (
        await session.execute(select(User).where(User.role == "superadmin"))
    ).scalars().all()
    assert len(supers) == 1
