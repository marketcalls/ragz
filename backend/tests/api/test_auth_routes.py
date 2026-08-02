import hashlib
import hmac

import httpx

from ragz.core.config import Settings
from ragz.modules.auth.models import User
from ragz.modules.auth.service import _hash


async def test_login_ok(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    assert r.status_code == 200
    assert r.json()["access_token"]
    assert "refresh_token" in r.cookies


async def test_login_bad_password_problem_json(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "a@acme.com", "password": "bad"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Authentication failed"


async def test_refresh_and_logout(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    r2 = await client.post("/api/v1/auth/refresh")
    assert r2.status_code == 200
    assert r2.cookies["refresh_token"] != r.cookies["refresh_token"]
    r3 = await client.post("/api/v1/auth/logout")
    assert r3.status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_concurrent_tabs_grace_reissue(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    """Two tabs share one refresh cookie; the tab that loses the rotation race
    replays the just-rotated token within the grace window and must get a fresh
    pair (200) instead of tripping family revocation."""
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    original = r.cookies["refresh_token"]
    r2 = await client.post("/api/v1/auth/refresh")  # tab A wins the race
    assert r2.status_code == 200
    # tab B still holds the pre-rotation cookie
    client.cookies.set("refresh_token", original, path="/api/v1/auth")
    r3 = await client.post("/api/v1/auth/refresh")
    assert r3.status_code == 200
    assert r3.json()["access_token"]
    assert r3.cookies["refresh_token"] not in {original, r2.cookies["refresh_token"]}
    # tab A's cookie survived the race (family not revoked)
    client.cookies.set("refresh_token", r2.cookies["refresh_token"], path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


def test_hash_pepper_crypto() -> None:
    """_hash falls back to bare SHA-256 (backward compatible) and switches to
    HMAC-SHA256 keyed on the pepper when one is configured."""
    raw = "tok-abc-123"
    plain = hashlib.sha256(raw.encode()).hexdigest()
    assert _hash(raw) == plain
    assert _hash(raw, "") == plain
    peppered = _hash(raw, "s3cret-pepper")
    assert peppered == hmac.new(b"s3cret-pepper", raw.encode(), hashlib.sha256).hexdigest()
    assert peppered != plain
    assert _hash(raw, "different-pepper") != peppered


async def test_refresh_token_hash_is_peppered(
    client: httpx.AsyncClient, seeded_user: User, test_settings: Settings
) -> None:
    """With a pepper configured, the stored refresh-token hash is keyed by it at
    BOTH write (login) and read (refresh) — proven by rotating the pepper, which
    must invalidate the existing session (the documented rotation behavior)."""
    test_settings.api_key_pepper = "pepper-A"
    login = await client.post(
        "/api/v1/auth/login", json={"email": "a@acme.com", "password": "pw123456"}
    )
    assert login.status_code == 200
    # read side uses the same pepper: refresh succeeds
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    # rotating the pepper makes the stored hash unverifiable -> session dies
    test_settings.api_key_pepper = "pepper-B"
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
