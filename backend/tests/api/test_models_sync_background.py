import httpx

from raghub.modules.auth.models import User

OPENAI_BODY = {
    "litellm_model_name": "gpt-4o-mini", "display_name": "GPT-4o mini",
    "provider_kind": "openai", "api_key": "sk-live-abc",
}


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_model_create_returns_before_sync_completes(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    """POST /api/v1/admin/models responds 201; the replay to LiteLLM happens as a
    background task (ASGITransport runs background tasks after the response), and
    the row ends in sync_status='synced'."""
    h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    assert r.status_code == 201
    # The response was serialized before the background replay ran, so it still
    # carries the row's pre-sync default rather than the post-replay outcome.
    assert r.json()["sync_status"] == "pending"

    listing = await client.get("/api/v1/admin/models", headers=h)
    assert listing.json()[0]["sync_status"] == "synced"


async def test_model_delete_triggers_background_sync(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    created = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    model_id = created.json()["id"]

    other = await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h,
    )
    other_id = other.json()["id"]

    r = await client.delete(f"/api/v1/admin/models/{model_id}", headers=h)
    assert r.status_code == 204

    listing = (await client.get("/api/v1/admin/models", headers=h)).json()
    assert [m["id"] for m in listing] == [other_id]
    assert listing[0]["sync_status"] == "synced"
