import httpx
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.tenancy.context import TenantContext, get_tenant_context, require_role


def wire_probe(app: FastAPI) -> None:
    @app.get("/probe/me")
    async def me(ctx: TenantContext = Depends(get_tenant_context)) -> dict[str, str]:  # noqa: B008
        return {"role": ctx.role, "org_id": str(ctx.org_id)}

    @app.get("/probe/admin", dependencies=[Depends(require_role("admin"))])
    async def admin_only() -> dict[str, bool]:
        return {"ok": True}


async def login_token(client: httpx.AsyncClient, email: str, pw: str = "pw123456") -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return str(r.json()["access_token"])


async def test_me_requires_token(client: httpx.AsyncClient, seeded_user: User) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    assert (await client.get("/probe/me")).status_code == 401
    tok = await login_token(client, "a@acme.com")
    r = await client.get("/probe/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["role"] == "admin"


async def test_role_guard(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    plain = User(
        org_id=seeded_user.org_id,
        email="p@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(plain)
    await session.commit()
    admin_tok = await login_token(client, "a@acme.com")
    user_tok = await login_token(client, "p@acme.com")
    admin_headers = {"Authorization": f"Bearer {admin_tok}"}
    user_headers = {"Authorization": f"Bearer {user_tok}"}
    assert (await client.get("/probe/admin", headers=admin_headers)).status_code == 200
    assert (await client.get("/probe/admin", headers=user_headers)).status_code == 403
