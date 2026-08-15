"""sec RAGZ-PUB-06 (session inventory + revocation): a live session is a
refresh-token FAMILY. Covers GET /auth/sessions (list, current-session
marking), DELETE /auth/sessions/{family_id} (scoped single revoke), and
POST /auth/sessions/revoke-others (spare-current bulk revoke)."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.tenancy.models import Organization


async def _login(
    client: httpx.AsyncClient, email: str, password: str = "pw123456"  # noqa: S107
) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _second_user(session: AsyncSession, seeded_user: User) -> User:
    org = Organization(name="Other Org")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="b@other.example",
        password_hash=hash_password("pw123456"), role="admin",
    )
    session.add(user)
    await session.commit()
    return user


async def test_list_sessions_marks_caller_cookie_as_current(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    login_a = await _login(client, seeded_user.email)
    assert login_a.status_code == 200
    cookie_a = login_a.cookies["refresh_token"]
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

    # Second session: swap the cookie jar so login #2 doesn't clobber #1's
    # cookie server-side (each login mints a brand-new family; the jar just
    # needs to carry #2's cookie for the subsequent listing call).
    login_b = await _login(client, seeded_user.email)
    assert login_b.status_code == 200
    cookie_b = login_b.cookies["refresh_token"]
    assert cookie_b != cookie_a

    # Client cookie jar now holds cookie_b (the most recent Set-Cookie).
    r = await client.get("/api/v1/auth/sessions", headers=headers_a)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    current = [s for s in body if s["current"]]
    assert len(current) == 1
    non_current = [s for s in body if not s["current"]]
    assert len(non_current) == 1
    for s in body:
        assert {"family_id", "created_at", "last_used_at", "expires_at", "current"} <= s.keys()

    # Switching the jar to cookie_a flips which family is "current".
    client.cookies.set("refresh_token", cookie_a, path="/api/v1/auth")
    r2 = await client.get("/api/v1/auth/sessions", headers=headers_a)
    assert r2.status_code == 200
    current_families_2 = {s["family_id"] for s in r2.json() if s["current"]}
    current_families_1 = {s["family_id"] for s in body if s["current"]}
    assert current_families_2 != current_families_1


async def test_list_sessions_no_cookie_marks_none_current(
    client: httpx.AsyncClient, seeded_user: User, user_headers: dict[str, str]
) -> None:
    # user_headers logs in once (leaving a cookie in the jar); explicitly
    # drop it to simulate a bearer-only caller (e.g. a non-browser client).
    client.cookies.delete("refresh_token")
    r = await client.get("/api/v1/auth/sessions", headers=user_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["current"] is False


async def test_revoke_session_kills_that_family_only(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    login_a = await _login(client, seeded_user.email)
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
    cookie_a = login_a.cookies["refresh_token"]

    login_b = await _login(client, seeded_user.email)
    cookie_b = login_b.cookies["refresh_token"]

    # Jar currently holds cookie_b (login_b's Set-Cookie is the most recent),
    # so this listing call marks login_b's family "current" -- revoke that
    # one explicitly by family_id.
    r = await client.get("/api/v1/auth/sessions", headers=headers_a)
    families = {s["family_id"]: s for s in r.json()}
    target_family = next(fid for fid, s in families.items() if s["current"])

    d = await client.delete(f"/api/v1/auth/sessions/{target_family}", headers=headers_a)
    assert d.status_code == 204

    # The revoked family's refresh token no longer rotates.
    client.cookies.set("refresh_token", cookie_b, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    # The current (untouched) family still works.
    client.cookies.set("refresh_token", cookie_a, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_revoke_other_sessions_spares_current(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    login_a = await _login(client, seeded_user.email)
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
    cookie_a = login_a.cookies["refresh_token"]

    login_b = await _login(client, seeded_user.email)
    cookie_b = login_b.cookies["refresh_token"]
    login_c = await _login(client, seeded_user.email)
    cookie_c = login_c.cookies["refresh_token"]

    # Jar currently holds cookie_c; make cookie_a "current" for this call.
    client.cookies.set("refresh_token", cookie_a, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers_a)
    assert r.status_code == 204

    client.cookies.set("refresh_token", cookie_a, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    client.cookies.set("refresh_token", cookie_b, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    client.cookies.set("refresh_token", cookie_c, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_cannot_revoke_another_users_session(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    other = await _second_user(session, seeded_user)

    victim_login = await _login(client, seeded_user.email)
    victim_family_resp = await client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {victim_login.json()['access_token']}"},
    )
    victim_family = victim_family_resp.json()[0]["family_id"]

    attacker_login = await _login(client, other.email)
    attacker_headers = {"Authorization": f"Bearer {attacker_login.json()['access_token']}"}

    r = await client.delete(f"/api/v1/auth/sessions/{victim_family}", headers=attacker_headers)
    assert r.status_code == 404

    # The victim's session is untouched.
    client.cookies.set("refresh_token", victim_login.cookies["refresh_token"], path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_sessions_routes_require_auth(client: httpx.AsyncClient) -> None:
    from uuid import uuid4

    assert (await client.get("/api/v1/auth/sessions")).status_code == 401
    assert (await client.delete(f"/api/v1/auth/sessions/{uuid4()}")).status_code == 401
    assert (await client.post("/api/v1/auth/sessions/revoke-others")).status_code == 401
