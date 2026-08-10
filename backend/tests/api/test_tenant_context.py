import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.context import (
    TenantContext,
    build_context_for_user,
    get_tenant_context,
    require_role,
)
from ragz.modules.tenancy.models import RoleTemplate


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


async def test_superadmin_bypasses_role_guard(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    wire_probe(client._transport.app)  # type: ignore[attr-defined]
    superadmin = User(
        org_id=seeded_user.org_id,
        email="root@acme.com",
        password_hash=seeded_user.password_hash,
        role="superadmin",
    )
    session.add(superadmin)
    await session.commit()
    tok = await login_token(client, "root@acme.com")
    r = await client.get("/probe/admin", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


# --- RBAC-04: non-destructive default + fail-closed dangling role reference ---
# NOTE: `seeded_user` is an ADMIN (role="admin"), which always resolves to the
# full PERMISSIONS catalog and never reaches the custom_role_id branch. These
# tests need a plain role="user" account, so we create one in the seeded org.
@pytest.fixture
async def plain_user(seeded_user: User, session: AsyncSession) -> User:
    user = User(
        org_id=seeded_user.org_id,
        email="plain@acme.com",
        password_hash=seeded_user.password_hash,
        role="user",
    )
    session.add(user)
    await session.flush()
    return user


async def test_dangling_custom_role_id_fails_closed_not_broad_default(
    session: AsyncSession, plain_user: User
) -> None:
    # A role template referenced by custom_role_id but no longer present --
    # the DB FK (ON DELETE SET NULL) makes this impossible via the app's own
    # write paths (persisting a dangling id raises ForeignKeyViolationError),
    # so we reproduce the corrupted/out-of-band state IN MEMORY and suppress
    # autoflush while the context is built. A dangling reference must never
    # silently widen access back to DEFAULT_USER_PERMISSIONS (RBAC-04's core
    # defect).
    plain_user.custom_role_id = uuid.uuid4()  # no such RoleTemplate row exists
    with session.sync_session.no_autoflush:
        ctx = await build_context_for_user(session, plain_user)
    assert ctx.permissions == frozenset()


async def test_valid_custom_role_still_applies_normally(
    session: AsyncSession, plain_user: User
) -> None:
    template = RoleTemplate(name="t1", permissions=["search.execute", "chat.read"])
    session.add(template)
    await session.flush()
    plain_user.custom_role_id = template.id
    await session.flush()
    ctx = await build_context_for_user(session, plain_user)
    assert ctx.permissions == frozenset({"search.execute", "chat.read"})


async def test_no_custom_role_gets_non_destructive_default(
    session: AsyncSession, plain_user: User
) -> None:
    plain_user.custom_role_id = None
    await session.flush()
    ctx = await build_context_for_user(session, plain_user)
    assert "documents.upload" not in ctx.permissions
    assert "documents.delete" not in ctx.permissions
    assert "chat.use" not in ctx.permissions
    assert "search.execute" in ctx.permissions and "chat.generate" in ctx.permissions


async def test_dangling_reference_logs_high_severity(
    session: AsyncSession, plain_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dangling reference must be surfaced LOUDLY (structlog `error`, the
    # high-severity level). We swap the module logger for a capturing double
    # that only records `.error(...)` calls -- deterministic regardless of the
    # global structlog config other tests leave behind (a cached lazy proxy on
    # the module `_log` makes structlog.configure(...) unreliable here).
    errors: list[dict[str, object]] = []

    class _RecordingLogger:
        def error(self, event: str, **kw: object) -> None:
            errors.append({"event": event, **kw})

        def __getattr__(self, _name: str) -> object:
            return lambda *a, **k: None

    monkeypatch.setattr(
        "ragz.modules.tenancy.context._log", _RecordingLogger()
    )
    plain_user.custom_role_id = uuid.uuid4()
    with session.sync_session.no_autoflush:
        await build_context_for_user(session, plain_user)
    assert any(e.get("event") == "tenancy.dangling_custom_role_id" for e in errors)
