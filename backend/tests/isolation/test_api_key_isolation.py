"""Adversarial isolation suite for the external API-key surface (Tasks 1-4:
api_keys_service, ApiKeyDep/api_key_context, POST /external/v1/chat).

The retrieval-time tenant/workspace/ACL filter itself is already locked by
test_tenant_isolation.py and test_acl_isolation.py (unit-level, calling
retrieve() directly). This file's job is different: prove that the NEW
code -- api_key_context resolving a raw key into a TenantContext, and the
external chat route wiring that ctx into the exact same stream_reply/
retrieve() path chats.py uses -- doesn't introduce a SECOND, unguarded way
to reach another tenant's/workspace's/ACL-restricted content. If any test
here fails, treat it as a security incident, not a flake.

Cases 1-3 go through the REAL retrieval pipeline (real Postgres, real
Qdrant, real ingest -- retriever/chunk_reader left at their production
defaults) with only the LLM generation stubbed out, via `EchoStreamer`: a
probe streamer that echoes back exactly the rendered <data> blocks the
route assembled from `sources` (the ONLY path retrieved-chunk text can
reach an external caller through), plus a `[1]` citation marker whenever
sources exist. Whatever leaked past the retrieval-time filter is exactly
what shows up in the JSON response -- this is a strictly MORE sensitive
probe than trusting a real LLM to faithfully reproduce or omit a secret.

Cases 4-5 don't need real retrieval at all (a revoked/expired key must
401 before `api_key_context` ever resolves a workspace), so they reuse the
lightweight FakeRetriever/FakeStreamer app from test_external_chat.py.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_session_factory
from ragz.modules.audit.models import AuditEvent
from ragz.modules.audit.service import record_audit
from ragz.modules.auth.api_keys_service import generate_api_key, revoke_api_key
from ragz.modules.auth.models import ApiKey, User
from ragz.modules.chat.llm import LLMDelta, LLMUsage
from ragz.modules.documents.models import Document
from ragz.modules.documents.service import set_document_acl
from ragz.modules.models.models import Model
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.models import Workspace, WorkspaceMember
from tests.conftest import FakeRetriever, FakeStreamer, _stub_litellm_handler
from tests.isolation.conftest import ingest_text, seed_acl_workspace, seed_same_org_two_workspaces

RESTRICTED = "finance secret: the acquisition price is 4400"
UNRESTRICTED = "cafeteria notice: the lunch menu changes on friday"

pytest_plugins = ["tests.api.test_external_chat"]


class EchoStreamer:
    """Isolation-suite probe (see module docstring): echoes back exactly the
    <data>-block portion of the final prompt message, never the trailing
    "Question: ..." tail -- that tail carries the caller's raw question
    text verbatim regardless of retrieval, so echoing it too would make a
    query that quotes the excluded secret as its own lure look like a
    leak. Appends a `[1]` marker whenever at least one source was
    rendered, which parse_citation_markers/stream_reply's own citation-
    building always resolves to sources[0] -- itself guaranteed to be an
    ALLOWED chunk, since excluded content never becomes a source at all."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    async def stream(  # type: ignore[no-untyped-def]
        self, *, model: str, messages: list[dict[str, str]], reasoning_effort: str | None = None
    ):
        self.calls.append(messages)
        content = str(messages[-1]["content"])
        data_only = content.split("\n\nQuestion:", 1)[0]
        text = data_only if "<data " in data_only else "no sources"
        if "<data " in data_only:
            text += " [1]"
        yield LLMDelta(text)
        yield LLMUsage(prompt_tokens=1, completion_tokens=1)


@pytest.fixture
def echo_streamer() -> EchoStreamer:
    return EchoStreamer()


@pytest.fixture
def ext_settings(stack_env: None, kek_file: str) -> Settings:
    """Depends on `stack_env` explicitly (not just transitively via a sibling
    fixture) so pytest's per-fixture dependency contract guarantees
    stack_env's env-var monkeypatching has already run before Settings()
    reads them -- sibling fixtures in the same test's parameter list give no
    such ordering guarantee."""
    return Settings(_env_file=None, kek_file=kek_file)


@pytest.fixture
async def ext_app_client(
    engine: AsyncEngine, redis_client: Redis, ext_settings: Settings,
    qdrant_collection: None, echo_streamer: EchoStreamer,
) -> AsyncIterator[httpx.AsyncClient]:
    """The real-retrieval external-API client: retriever/chunk_reader are
    left at create_app's production defaults (real retrieve()/
    RetrievalChunkReader), only the LLM generation step is stubbed."""
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        llm_streamer=echo_streamer,
    )
    app.dependency_overrides[get_settings] = lambda: ext_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def light_env(session: AsyncSession, seeded_user: User) -> dict[str, object]:
    """Workspace + membership + default model + one real (Postgres) indexed
    Document -- mirrors tests/conftest.py's chat_env fixture. FakeRetriever
    returns chunks tagged with this document's id, and _source_refs'
    get_document_checked (chat/service.py) 404s hard on ANY document_id that
    doesn't resolve to a real row for ctx.org_id, so the retriever's
    document_id must point at something real or every call here 502s before
    ever reaching the revoked/expired/leakage assertions."""
    ws = Workspace(org_id=seeded_user.org_id, name="LightWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    model = Model(litellm_model_name=f"light-{ws.id}", display_name="Light",
                  provider_kind="ollama", enabled=True)
    session.add(model)
    await session.flush()
    ws.default_model_id = model.id
    doc = Document(org_id=seeded_user.org_id, workspace_id=ws.id, filename="light.pdf",
                   mime="application/pdf", size_bytes=10, content_hash="h", status="indexed",
                   storage_key="k", created_by=seeded_user.id, lineage_id=uuid4())
    session.add_all([ws, doc])
    await session.commit()
    return {"workspace": ws, "document": doc}


@pytest.fixture
async def light_client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings,
    light_env: dict[str, object],
) -> AsyncIterator[httpx.AsyncClient]:
    """Lightweight app (FakeRetriever/FakeStreamer, no real Qdrant) for the
    revoked/expired/no-leakage cases, which must never reach retrieval at
    all -- api_key_context rejects them first."""
    document = light_env["document"]
    assert isinstance(document, Document)
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
        retriever=FakeRetriever(document.id),
        llm_streamer=FakeStreamer(),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _mint_key(
    session: AsyncSession, settings: Settings, ctx: TenantContext, ws: Workspace,
    *, expires_at: datetime | None = None,
) -> str:
    """Sets a chat default model on `ws` (so model resolution succeeds) and
    mints a real API key scoped to that workspace for ctx's user, calling
    generate_api_key directly -- exactly like test_external_chat.py's
    _make_key, minus the HTTP round-trip through the superadmin route,
    since these tests seed everything else via the ORM too. A no-op on the
    model/default_model_id step if `ws` already has a default model (this
    helper may be called more than once for the same workspace, e.g. to
    mint keys for two different users of one ACL-restricted workspace)."""
    if ws.default_model_id is None:
        model = Model(litellm_model_name=f"iso-key-{uuid4()}", display_name="Iso",
                      provider_kind="ollama", enabled=True)
        session.add(model)
        await session.flush()
        ws.default_model_id = model.id
        session.add(ws)
        await session.commit()
    _, raw = await generate_api_key(
        session, settings, actor_id=ctx.user_id, name="iso-test-key",
        user_id=ctx.user_id, workspace_id=ws.id, expires_at=expires_at,
    )
    return raw


async def test_cross_workspace_content_never_leaks_via_plain_member_key(
    ext_app_client: httpx.AsyncClient, ext_settings: Settings, session: AsyncSession,
) -> None:
    """A key bound to workspace A (same org as B) -- the real product-leak
    scenario. Both users are plain "user"-role members of their own
    workspace only (seed_same_org_two_workspaces), so this is the plain-
    member key case the brief calls out (distinct from the ALREADY-covered
    admin-key cross-workspace *conversation_id* escape in
    test_external_chat.py). min_score is forced to 0.0 on both workspaces
    so a weak dense-cosine score between the two secrets' hash embeddings
    can never trip the no_answer/decline branch and starve the probe of a
    real <data> block to inspect -- unrelated to the security property
    under test, which is retrieval-time exclusion, not answer quality."""
    ctx1, ws1, ctx2, ws2 = await seed_same_org_two_workspaces(session)
    ws1.min_score = 0.0
    ws2.min_score = 0.0
    session.add_all([ws1, ws2])
    await session.commit()

    content_a = "workspace A external-key secret: the override token is ALPHA-9911"
    content_b = "workspace B external-key secret: the override token is BRAVO-4477"
    doc_a = await ingest_text(session, ctx1, ws1, "a.txt", content_a)
    await ingest_text(session, ctx2, ws2, "b.txt", content_b)

    raw = await _mint_key(session, ext_settings, ctx1, ws1)

    r = await ext_app_client.post(
        "/external/v1/chat", json={"question": content_b},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "BRAVO-4477" not in body["answer"]
    assert "ALPHA-9911" in body["answer"]  # not a vacuous empty-sources pass
    assert body["citations"]
    assert all(c["document_id"] == str(doc_a.id) for c in body["citations"])


async def test_cross_tenant_content_never_leaks_via_key(
    ext_app_client: httpx.AsyncClient, ext_settings: Settings, session: AsyncSession,
    two_orgs: dict,  # type: ignore[type-arg]
) -> None:
    """A key for org1 cannot retrieve org2's content even when queried with
    org2's exact secret as the strongest possible lure -- the
    TenantContext.org_id/workspace_id filter (derived from the key's OWN
    user via api_key_context/build_context_for_user) excludes it, exactly
    like it does for a JWT-authenticated caller. `two_orgs` builds each
    org's document via the real ingest pipeline under its OWN ctx
    (ingest_text), never through create_from_upload with a forged org_id --
    unlike the folder-subtree walk this suite's other adversarial tests
    mirror, retrieval's security boundary is the Qdrant payload's tenant_id
    tag, which the write path can never mismatch against its own ctx.org_id;
    forging a Postgres Document row's org_id after the fact wouldn't touch
    that boundary at all, so the meaningful adversarial construction here is
    two independently-real tenants queried through the NEW api-key route,
    not a corrupted row."""
    ctx_a, ws_a, doc_a = two_orgs["a"]

    raw = await _mint_key(session, ext_settings, ctx_a, ws_a)

    r = await ext_app_client.post(
        "/external/v1/chat",
        json={"question": "org bravo secret: the vault code is 9962"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "9962" not in body["answer"]
    assert "7431" in body["answer"]  # org alpha's own vault code -- non-vacuous
    assert body["citations"]
    assert all(c["document_id"] == str(doc_a.id) for c in body["citations"])


async def test_acl_restricted_document_never_appears_in_external_citations(
    ext_app_client: httpx.AsyncClient, ext_settings: Settings, session: AsyncSession,
) -> None:
    """A document restricted to a group the key's user is NOT in must never
    surface in the external answer or its citations -- reuses the exact
    vector-query ACL clause test_acl_isolation.py already pins, but through
    the NEW api-key route rather than a direct retrieve() call, proving
    api_key_context threads the key user's real group_ids into ctx (a
    dropped/empty group set here would silently make the ACL clause a
    no-op via the fail-open direction, not fail-closed). The unrestricted
    doc is indexed too, so a non-empty answer/citation proves the exclusion
    is selective, not a blanket empty-sources fluke."""
    ctx_in, ctx_out, ctx_admin, ws, finance = await seed_acl_workspace(session)
    # Forced to 0.0 for the same reason as the cross-workspace test above:
    # once the ACL filter excludes the restricted chunk, the outsider's only
    # remaining candidate (the unrestricted doc) may score low against the
    # RESTRICTED lure text under the hash embedder, and a real min_score
    # threshold would trip the no_answer/general_knowledge branch and starve
    # this probe of a real <data> block -- unrelated to the ACL exclusion
    # this test is actually pinning.
    ws.min_score = 0.0
    session.add(ws)
    await session.commit()
    restricted = await ingest_text(session, ctx_admin, ws, "restricted.txt", RESTRICTED)
    await set_document_acl(session, ctx_admin, restricted.id, [finance.id])
    open_doc = await ingest_text(session, ctx_admin, ws, "open.txt", UNRESTRICTED)

    raw = await _mint_key(session, ext_settings, ctx_out, ws)  # outsider: not in "finance"

    r = await ext_app_client.post(
        "/external/v1/chat", json={"question": RESTRICTED},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "4400" not in body["answer"]
    assert all(c["document_id"] != str(restricted.id) for c in body["citations"])
    # Not vacuous: the unrestricted doc's content DID make it through.
    assert "lunch menu" in body["answer"]
    assert body["citations"]
    assert any(c["document_id"] == str(open_doc.id) for c in body["citations"])

    # Not vacuous from the other direction either: the group member's OWN
    # key resolves the restricted doc fine -- the exclusion above is really
    # about group membership, not e.g. a broken workspace/model wiring that
    # would have hidden it from everyone.
    raw_in = await _mint_key(session, ext_settings, ctx_in, ws)
    r_in = await ext_app_client.post(
        "/external/v1/chat", json={"question": RESTRICTED},
        headers={"Authorization": f"Bearer {raw_in}"},
    )
    assert r_in.status_code == 200, r_in.text
    assert "4400" in r_in.json()["answer"]


async def test_revoked_key_returns_401(
    light_client: httpx.AsyncClient, light_env: dict[str, object], session: AsyncSession,
    seeded_user: User, test_settings: Settings,
) -> None:
    ws = light_env["workspace"]
    assert isinstance(ws, Workspace)
    row, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="revoke-me",
        user_id=seeded_user.id, workspace_id=ws.id, expires_at=None,
    )
    r_before = await light_client.post(
        "/external/v1/chat", json={"question": "does this key still work right now"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r_before.status_code == 200, r_before.text  # sanity: not vacuous

    await revoke_api_key(session, key_id=row.id)

    r_after = await light_client.post(
        "/external/v1/chat", json={"question": "should be denied after revocation"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r_after.status_code == 401


async def test_expired_key_returns_401(
    light_client: httpx.AsyncClient, light_env: dict[str, object], session: AsyncSession,
    seeded_user: User, test_settings: Settings,
) -> None:
    ws = light_env["workspace"]
    assert isinstance(ws, Workspace)
    past = datetime.now(UTC) - timedelta(hours=1)
    _, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="already-expired",
        user_id=seeded_user.id, workspace_id=ws.id, expires_at=past,
    )
    r = await light_client.post(
        "/external/v1/chat", json={"question": "should be denied: key already expired"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 401


async def test_no_key_or_hash_leaks_into_response_or_audit(
    light_client: httpx.AsyncClient, light_env: dict[str, object], session: AsyncSession,
    seeded_user: User, test_settings: Settings,
) -> None:
    """Iron rule 3: the raw key and its peppered hash must never appear in
    the external call's response body, nor in any AuditEvent row written
    around the key's lifecycle (created/revoked -- api_keys.py's own
    record_audit calls). ApiKeyOut already excludes key_hash by construction
    (schemas.py), and record_audit only ever writes target_id=str(key_id),
    but this pins the actual runtime values, not just the schema shape."""
    ws = light_env["workspace"]
    assert isinstance(ws, Workspace)
    row, raw = await generate_api_key(
        session, test_settings, actor_id=seeded_user.id, name="leak-check",
        user_id=seeded_user.id, workspace_id=ws.id, expires_at=None,
    )
    await record_audit(
        session, org_id=seeded_user.org_id, actor_id=seeded_user.id,
        action="api_key.created", target_type="api_key", target_id=str(row.id),
    )
    await session.commit()

    r = await light_client.post(
        "/external/v1/chat", json={"question": "a perfectly normal question"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200, r.text
    assert raw not in r.text
    assert row.key_hash not in r.text

    await revoke_api_key(session, key_id=row.id)
    await record_audit(
        session, org_id=seeded_user.org_id, actor_id=seeded_user.id,
        action="api_key.revoked", target_type="api_key", target_id=str(row.id),
    )
    await session.commit()

    audit_rows = (await session.execute(select(AuditEvent))).scalars().all()
    assert audit_rows  # not a vacuous pass: rows actually exist
    for event in audit_rows:
        haystack = f"{event.action} {event.target_type} {event.target_id}"
        assert raw not in haystack
        assert row.key_hash not in haystack

    persisted = (
        await session.execute(select(ApiKey).where(ApiKey.id == row.id))
    ).scalar_one()
    assert persisted.key_hash != raw  # envelope hash, never plaintext
