import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.modules.auth.models import User
from raghub.modules.models.catalog import ModelCatalogEntry


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


OPENAI_BODY = {
    "litellm_model_name": "gpt-4o-mini", "display_name": "GPT-4o mini",
    "provider_kind": "openai", "api_key": "sk-live-abc",
}


async def test_superadmin_crud_and_key_never_returned(
    client: httpx.AsyncClient, seeded_superadmin: User
) -> None:
    h = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h)
    assert r.status_code == 201
    assert "sk-live-abc" not in r.text  # write-only key
    created = r.json()
    model_id = created["id"]
    # Plan D admin-page fields: fingerprint (never the key) + gateway sync outcome.
    assert created["key_fingerprint"].startswith("...-abc sha256:")
    # Plan G: the LiteLLM replay is now a background task, so the create response
    # reflects the row's pre-sync default; sync_status='synced' shows up once the
    # replay (scheduled on the same request) has run - the admin page's next fetch.
    assert created["sync_status"] == "pending"

    r = await client.patch(f"/api/v1/admin/models/{model_id}",
                           json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False

    listing = await client.get("/api/v1/admin/models", headers=h)
    assert "sk-live-abc" not in listing.text
    assert [m["id"] for m in listing.json()] == [model_id]
    assert listing.json()[0]["sync_status"] == "synced"  # background replay succeeded

    assert (await client.delete(f"/api/v1/admin/models/{model_id}", headers=h)).status_code == 204
    assert (await client.get("/api/v1/admin/models", headers=h)).json() == []


async def test_admin_role_denied_but_can_list_public(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User
) -> None:
    h_super = await auth(client, "root@platform.example")
    r = await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h_super)
    model_id = r.json()["id"]
    await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h_super,
    )
    await client.patch(f"/api/v1/admin/models/{model_id}", json={"enabled": False},
                       headers=h_super)

    admin_listing = (await client.get("/api/v1/admin/models", headers=h_super)).json()
    llama = next(m for m in admin_listing if m["display_name"] == "Llama")
    assert llama["key_fingerprint"] is None  # keyless provider

    h_admin = await auth(client, "a@acme.com")
    assert (await client.post("/api/v1/admin/models", json=OPENAI_BODY,
                              headers=h_admin)).status_code == 403
    public = await client.get("/api/v1/models", headers=h_admin)
    assert public.status_code == 200
    assert [m["display_name"] for m in public.json()] == ["Llama"]  # enabled only
    assert "litellm_model_name" not in public.text  # ModelPublic shape


async def test_workspace_default_model(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    h_super = await auth(client, "root@platform.example")
    r = await client.post(
        "/api/v1/admin/models",
        json={"litellm_model_name": "llama3", "display_name": "Llama",
              "provider_kind": "ollama", "base_url": "http://ollama:11434"},
        headers=h_super,
    )
    model_id = r.json()["id"]

    h_admin = await auth(client, "a@acme.com")
    ws = await client.post("/api/v1/workspaces", json={"name": "Fin"}, headers=h_admin)
    ws_id = ws.json()["id"]
    r = await client.patch(f"/api/v1/workspaces/{ws_id}",
                           json={"default_model_id": model_id}, headers=h_admin)
    assert r.status_code == 200 and r.json()["default_model_id"] == model_id

    import uuid
    bad = str(uuid.uuid4())
    r = await client.patch(f"/api/v1/workspaces/{ws_id}",
                           json={"default_model_id": bad}, headers=h_admin)
    assert r.status_code == 404


async def test_catalog_listing_flags_unregistered_models(
    client: httpx.AsyncClient, seeded_user: User, seeded_superadmin: User,
    session: AsyncSession,
) -> None:
    """MODEL-10/G7: GET /admin/models/catalog cross-references the registry so
    the admin UI can surface "N new models available"."""
    h_super = await auth(client, "root@platform.example")
    await client.post("/api/v1/admin/models", json=OPENAI_BODY, headers=h_super)  # registers it

    session.add_all([
        ModelCatalogEntry(name="gpt-4o-mini", provider="openai", max_input_tokens=128000,
                          input_cost_per_token=0.00000015, output_cost_per_token=0.0000006,
                          source="snapshot"),
        ModelCatalogEntry(name="claude-3-haiku", provider="anthropic", max_input_tokens=200000,
                          input_cost_per_token=0.00000025, output_cost_per_token=0.00000125,
                          source="snapshot"),
    ])
    await session.commit()

    r = await client.get("/api/v1/admin/models/catalog", headers=h_super)
    assert r.status_code == 200
    body = r.json()
    assert body["new_available"] == 1  # claude-3-haiku only; gpt-4o-mini is registered
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["gpt-4o-mini"]["registered"] is True
    assert by_name["claude-3-haiku"]["registered"] is False
    assert by_name["claude-3-haiku"]["input_cost_per_1m"] == 0.25

    h_admin = await auth(client, "a@acme.com")
    assert (await client.get("/api/v1/admin/models/catalog",
                             headers=h_admin)).status_code == 403
    assert (await client.post("/api/v1/admin/models/catalog/refresh",
                              headers=h_admin)).status_code == 403
