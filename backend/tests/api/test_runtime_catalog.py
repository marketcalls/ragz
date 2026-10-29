import httpx

from ragz.modules.auth.models import User


async def _headers(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "pw123456"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_runtime_catalog_has_latest_subscription_and_embedding_models(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await _headers(client, "root@platform.example")
    response = await client.get("/api/v1/admin/models/catalog/providers", headers=headers)
    assert response.status_code == 200
    assert response.json()["litellm_version"] == "1.106.6"
    assert len(response.json()["providers"]) > 90
    response = await client.get(
        "/api/v1/admin/models/catalog/models?provider=chatgpt&mode=chat",
        headers=headers,
    )
    assert response.status_code == 200
    models = {m["id"]: m for m in response.json()["models"]}
    assert "chatgpt/gpt-6-astra" in models
    assert models["chatgpt/gpt-5.6-luna"]["billing_mode"] == "subscription"
    assert models["chatgpt/gpt-6-astra"]["input_cost_per_1m"] is None
    response = await client.get(
        "/api/v1/admin/models/catalog/models?provider=openai&mode=embedding",
        headers=headers,
    )
    assert response.status_code == 200
    assert any(m["id"] == "text-embedding-3-small" for m in response.json()["models"])


async def test_catalog_and_model_test_require_superadmin(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> None:
    headers = await _headers(client, "a@acme.com")
    for path in ("providers", "models"):
        response = await client.get(f"/api/v1/admin/models/catalog/{path}", headers=headers)
        assert response.status_code == 403
    response = await client.post(
        "/api/v1/admin/models/00000000-0000-0000-0000-000000000001/test",
        headers=headers,
    )
    assert response.status_code == 403


async def test_latest_reasoning_metadata_reaches_public_picker(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await _headers(client, "root@platform.example")
    response = await client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={
            "litellm_model_name": "chatgpt/gpt-6-astra",
            "display_name": "Astra subscription",
            "provider_kind": "litellm",
            "default_reasoning_effort": "ultra",
        },
    )
    assert response.status_code == 201
    assert response.json()["supports_reasoning"] is True
    assert response.json()["supports_vision"] is True
    response = await client.get("/api/v1/models", headers=headers)
    model = next(m for m in response.json() if m["model_name"] == "chatgpt/gpt-6-astra")
    assert model["billing_mode"] == "subscription"
    assert model["default_reasoning_effort"] == "ultra"
    assert "ultra" in model["supported_reasoning_efforts"]


async def test_model_validation_never_echoes_an_api_key(
    client: httpx.AsyncClient,
    seeded_superadmin: User,
) -> None:
    headers = await _headers(client, "root@platform.example")
    response = await client.post(
        "/api/v1/admin/models",
        headers=headers,
        json={
            "litellm_model_name": "chatgpt/gpt-6-astra",
            "display_name": "Astra",
            "provider_kind": "litellm",
            "api_key": "private-key-do-not-echo",
        },
    )
    assert response.status_code == 422
    assert "private-key-do-not-echo" not in response.text
