from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.models.models import Model


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Tuned"}, headers=h)
    assert r.status_code == 201
    return str(r.json()["id"])


async def test_new_workspace_has_retrieval_defaults(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = next(w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
              if w["id"] == ws_id)
    assert ws["top_k"] == 8
    assert ws["rerank_enabled"] is False
    assert ws["system_prompt_override"] is None


async def test_admin_updates_retrieval_settings(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}",
        json={"top_k": 12, "min_score": 0.5, "rerank_enabled": True,
              "system_prompt_override": "Answer in formal English."},
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["top_k"] == 12 and body["min_score"] == 0.5
    assert body["rerank_enabled"] is True
    assert body["system_prompt_override"] == "Answer in formal English."


async def test_explicit_null_clears_prompt_override_only(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    await client.patch(f"/api/v1/workspaces/{ws_id}",
                       json={"system_prompt_override": "x"}, headers=h)
    r = await client.patch(f"/api/v1/workspaces/{ws_id}",
                           json={"system_prompt_override": None}, headers=h)
    assert r.status_code == 200 and r.json()["system_prompt_override"] is None
    # null for a non-nullable setting is a 409, not a silent no-op
    r2 = await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": None}, headers=h)
    assert r2.status_code == 409


async def test_top_k_bounds_enforced(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    assert (await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 0},
                               headers=h)).status_code == 422
    assert (await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 51},
                               headers=h)).status_code == 422


async def test_non_admin_cannot_patch(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    plain = User(org_id=seeded_user.org_id, email="p@acme.com",
                 password_hash=seeded_user.password_hash, role="user")
    session.add(plain)
    await session.commit()
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    h_user = await auth(client, "p@acme.com")
    r = await client.patch(f"/api/v1/workspaces/{ws_id}", json={"top_k": 5}, headers=h_user)
    assert r.status_code == 403


async def test_patch_fallback_policy(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"fallback_policy": "decline"}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["fallback_policy"] == "decline"


async def test_fallback_policy_defaults_to_general_knowledge(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = next(w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
              if w["id"] == ws_id)
    assert ws["fallback_policy"] == "general_knowledge"


async def test_fallback_policy_rejects_unknown_value(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"fallback_policy": "hallucinate"}, headers=h
    )
    assert r.status_code == 422


async def test_fallback_policy_null_is_409(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"fallback_policy": None}, headers=h
    )
    assert r.status_code == 409


async def test_web_search_enabled_defaults_off_and_round_trips(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = next(w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
              if w["id"] == ws_id)
    assert ws["web_search_enabled"] is False
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"web_search_enabled": True}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["web_search_enabled"] is True


async def test_patch_strict_mode(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"strict_mode": True}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["strict_mode"] is True


async def test_strict_mode_defaults_to_false(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = next(w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
              if w["id"] == ws_id)
    assert ws["strict_mode"] is False


async def test_strict_mode_null_is_409(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"strict_mode": None}, headers=h
    )
    assert r.status_code == 409


async def test_patch_enrichment_enabled(client: httpx.AsyncClient, seeded_user: User) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": True}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["enrichment_enabled"] is True


async def test_enrichment_enabled_defaults_false(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = next(w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
              if w["id"] == ws_id)
    assert ws["enrichment_enabled"] is False


async def test_enrichment_enabled_null_is_409(
    client: httpx.AsyncClient, seeded_user: User
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": None}, headers=h
    )
    assert r.status_code == 409


async def test_patch_atomicity_with_mixed_valid_invalid_fields(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    """Verify that PATCH is atomic: if one field is invalid, no changes persist."""
    # Create a model for testing
    model = Model(
        litellm_model_name="gpt-4-test",
        display_name="GPT-4 Test",
        provider_kind="openai",
    )
    session.add(model)
    await session.commit()

    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)

    # Verify initial state by listing workspaces
    ws_before = next(
        w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
        if w["id"] == ws_id
    )
    assert ws_before["default_model_id"] is None
    assert ws_before["top_k"] == 8

    # Try PATCH with valid default_model_id + invalid top_k (null not allowed)
    # This should fail with 409 and not persist the default_model_id change
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}",
        json={"default_model_id": str(model.id), "top_k": None},
        headers=h,
    )
    assert r.status_code == 409
    assert "top_k cannot be null" in r.text or "cannot be null" in r.text

    # Verify that default_model_id was NOT persisted
    ws_after = next(
        w for w in (await client.get("/api/v1/workspaces", headers=h)).json()
        if w["id"] == ws_id
    )
    assert (
        ws_after["default_model_id"] is None
    ), "default_model_id should not change on validation failure"
    assert ws_after["top_k"] == 8, "top_k should remain unchanged"


# --- Plan K Task 7: backfill enqueued on a genuine False->True transition ---


@pytest.fixture
def captured_backfill(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    calls: list[UUID] = []
    monkeypatch.setattr(
        "ragz.api.routes.workspaces.enqueue_enrichment_backfill", calls.append
    )
    return calls


async def test_toggle_on_enqueues_backfill(
    client: httpx.AsyncClient, seeded_user: User, captured_backfill: list[UUID]
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": True}, headers=h
    )
    assert r.status_code == 200
    assert captured_backfill == [UUID(ws_id)]


async def test_toggle_already_on_does_not_reenqueue(
    client: httpx.AsyncClient, seeded_user: User, captured_backfill: list[UUID]
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r1 = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": True}, headers=h
    )
    assert r1.status_code == 200
    assert len(captured_backfill) == 1
    # PATCH with the same value is a no-op diff -> no re-enqueue
    r2 = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": True}, headers=h
    )
    assert r2.status_code == 200
    assert len(captured_backfill) == 1


async def test_toggling_off_then_on_does_not_enqueue_on_the_off_leg(
    client: httpx.AsyncClient, seeded_user: User, captured_backfill: list[UUID]
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    # Starts False; flipping to False again is a no-op diff, not a transition.
    r = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": False}, headers=h
    )
    assert r.status_code == 200
    assert captured_backfill == []
    # Now the real False->True transition fires exactly once.
    r2 = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": True}, headers=h
    )
    assert r2.status_code == 200
    assert captured_backfill == [UUID(ws_id)]
    # Toggling back OFF never enqueues (no code path — documented, not coded).
    r3 = await client.patch(
        f"/api/v1/workspaces/{ws_id}", json={"enrichment_enabled": False}, headers=h
    )
    assert r3.status_code == 200
    assert captured_backfill == [UUID(ws_id)]


async def test_list_members_route(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    from ragz.modules.tenancy.models import WorkspaceMember

    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=seeded_user.id, role="owner"))
    await session.commit()
    r = await client.get(f"/api/v1/workspaces/{ws_id}/members", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert {m["user_id"] for m in r.json()} == {str(seeded_user.id)}


async def test_remove_member_route_denies_final_owner(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession
) -> None:
    from ragz.modules.tenancy.models import WorkspaceMember

    h = await auth(client, seeded_user.email)
    ws_id = await make_workspace(client, h)
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=seeded_user.id, role="owner"))
    await session.commit()
    r = await client.delete(
        f"/api/v1/workspaces/{ws_id}/members/{seeded_user.id}", headers=h
    )
    assert r.status_code == 409
