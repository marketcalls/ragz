from uuid import UUID

import httpx

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import WorkspaceMember
from tests.api.test_documents_routes import auth, make_workspace
from tests.api.test_permissions_routes import make_templated_member
from tests.modules.retrieval.test_retrieve import upsert_texts


async def test_default_member_can_search(
    client: httpx.AsyncClient, seeded_user: User, session, qdrant_collection: None  # type: ignore[no-untyped-def]
) -> None:
    # RBAC-03: the new require_action("search.execute") gate must NOT regress a
    # plain role="user" member (no custom role -> DEFAULT_USER_PERMISSIONS, which
    # still includes search.execute). Proves the POSITIVE default-member path for
    # /search, complementing test_search_requires_search_execute_permission's 403
    # case for a narrow custom role that omits the action. Empty workspace -> a
    # real 200 no-answer, so success is decided by the gate, not by seeded data.
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    member = User(org_id=seeded_user.org_id, email="searcher@acme.com",
                  password_hash=seeded_user.password_hash, role="user")
    session.add(member)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=member.id))
    await session.commit()
    h_member = await auth(client, "searcher@acme.com")
    r = await client.post(f"/api/v1/workspaces/{ws_id}/search",
                          json={"query": "anything"}, headers=h_member)
    assert r.status_code == 200


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

    from ragz.modules.tenancy.context import TenantContext
    from ragz.modules.tenancy.models import Workspace

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


async def test_search_with_metadata_filter_excludes_non_matching_doc(
    client: httpx.AsyncClient, seeded_user: User, session, qdrant_collection: None  # type: ignore[no-untyped-def]
) -> None:
    from sqlalchemy import select

    from ragz.modules.documents.ingest import run_chunk, run_embed_upsert, run_parse
    from ragz.modules.documents.metadata import list_fields, set_document_metadata
    from ragz.modules.documents.service import create_from_upload
    from ragz.modules.retrieval.client import COLLECTION
    from ragz.modules.retrieval.service import update_document_current
    from ragz.modules.tenancy.context import TenantContext
    from ragz.modules.tenancy.models import Workspace

    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    ws = (await session.execute(select(Workspace))).scalar_one()
    ws.min_score = 0.0
    await session.commit()
    ctx = TenantContext(user_id=seeded_user.id, org_id=seeded_user.org_id,
                        role="admin", workspace_ids=frozenset())
    await list_fields(session, ctx, ws.id)  # seed presets incl doc_type

    async def _index(filename: str, text: str):  # type: ignore[no-untyped-def]
        doc = await create_from_upload(
            session, ctx, ws.id, filename=filename, mime="text/plain", data=text.encode()
        )
        await run_parse(doc.id)
        await run_chunk(doc.id)
        await run_embed_upsert(doc.id)
        await update_document_current(
            ctx.org_id, doc.id, is_current=True, collection_name=COLLECTION
        )
        await session.refresh(doc)
        return doc

    doc_policy = await _index("policy.txt", "quarterly safety review procedure")
    doc_manual = await _index(
        "manual.txt", "quarterly safety review procedure manual variant"
    )
    await set_document_metadata(session, ctx, doc_policy.id, {"doc_type": "policy"})
    await set_document_metadata(session, ctx, doc_manual.id, {"doc_type": "manual"})

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/search", headers=h,
        json={
            "query": "quarterly safety review procedure manual variant",  # manual's exact lure
            "metadata": {"doc_type": "policy"},
        },
    )
    assert r.status_code == 200
    doc_ids = {c["document_id"] for c in r.json()["chunks"]}
    assert str(doc_manual.id) not in doc_ids
    assert str(doc_policy.id) in doc_ids


async def test_search_unknown_metadata_field_is_404_problem_json(
    client: httpx.AsyncClient, seeded_user: User, qdrant_collection: None
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/search", headers=h,
        json={"query": "anything", "metadata": {"nonexistent_field": "x"}},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_search_requires_search_execute_permission(
    client: httpx.AsyncClient, seeded_user: User, session,  # type: ignore[no-untyped-def]
) -> None:
    # A custom role that carries NEITHER search.execute NOR documents.list --
    # only an unrelated, harmless permission (chat.read) -- must be denied
    # direct search access (RBAC-03: search.execute is now an explicit gate,
    # not implicit for any authenticated member).
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    await make_templated_member(
        session, seeded_user, email="narrow@acme.com", template_name="NoSearchOrList",
        permissions=["chat.read"], workspace_id=ws_id,
    )
    h_narrow = await auth(client, "narrow@acme.com")

    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/search", headers=h_narrow, json={"query": "test"}
    )
    assert r.status_code == 403
