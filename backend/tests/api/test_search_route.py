import httpx

from raghub.modules.auth.models import User
from tests.api.test_documents_routes import auth, make_workspace
from tests.modules.retrieval.test_retrieve import upsert_texts


async def test_search_empty_workspace_no_answer(
    client: httpx.AsyncClient, seeded_user: User, qdrant_collection: None
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.post(f"/api/v1/workspaces/{ws_id}/search",
                          json={"query": "anything"}, headers=h)
    assert r.status_code == 200
    assert r.json() == {"no_answer": True, "chunks": []}


async def test_search_returns_seeded_chunk(
    client: httpx.AsyncClient, seeded_user: User, session, qdrant_collection: None  # type: ignore[no-untyped-def]
) -> None:
    from sqlalchemy import select

    from raghub.modules.tenancy.context import TenantContext
    from raghub.modules.tenancy.models import Workspace

    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = (await session.execute(select(Workspace))).scalar_one()
    ws.min_score = 0.0
    await session.commit()
    ctx = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                        role="admin", workspace_ids=frozenset())
    await upsert_texts(ctx, ws, ["invoice 0231 covers the plutonium delivery"])

    r = await client.post(f"/api/v1/workspaces/{ws_id}/search",
                          json={"query": "invoice 0231"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["no_answer"] is False
    assert "invoice 0231" in body["chunks"][0]["text"]
    assert {"document_id", "page", "chunk_index", "text", "score"} <= set(body["chunks"][0])


async def test_search_requires_auth(client: httpx.AsyncClient, seeded_user: User) -> None:
    r = await client.post("/api/v1/workspaces/00000000-0000-0000-0000-000000000000/search",
                          json={"query": "x"})
    assert r.status_code == 401
