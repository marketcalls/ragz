import atexit
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.qdrant import QdrantContainer
from testcontainers.redis import RedisContainer

from ragz.api.app import create_app
from ragz.core.config import Settings, get_settings
from ragz.core.db import Base, build_engine, build_session_factory
from ragz.core.storage import ObjectStorage
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.chat.llm import LLMCompletion, LLMDelta, LLMUsage
from ragz.modules.chat.models import Chat
from ragz.modules.chat.web import WebResult
from ragz.modules.documents.models import Document
from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID, Model
from ragz.modules.retrieval.client import get_qdrant
from ragz.modules.retrieval.embeddings import get_dense_embedder
from ragz.modules.retrieval.service import RetrievalResult, RetrievedChunk
from ragz.modules.secrets.crypto import ensure_kek
from ragz.modules.tenancy.models import (
    Organization,
    RoleTemplate,
    Workspace,
    WorkspaceMember,
)

# RBAC-04: DEFAULT_USER_PERMISSIONS is now the non-destructive read floor.
# In production, the forward migration (c78eddf6863e) seeds a "Contributor"
# role carrying the old broad capability (upload/delete/chat) and assigns it
# to every pre-existing role="user" account, so nobody regresses. Tests build
# their schema via Base.metadata.create_all (migrations do NOT run), so any
# test account meant to behave like a normal pre-RBAC-04 contributor must be
# given this role EXPLICITLY -- exactly as the migration would. This list
# mirrors the migration's _CONTRIBUTOR_PERMISSIONS verbatim.
CONTRIBUTOR_PERMISSIONS = [
    "workspace.read",
    "documents.list", "documents.content.read", "documents.upload", "documents.delete",
    "documents.move", "documents.pin", "search.execute", "chat.read", "chat.generate",
    "chat.attachments.create", "chat.delete",
    # sec RAGZ-PUB-01 follow-on (migration f1a2b3c4d5e6): the folder CRUD routes
    # and the per-document metadata-value PUT now enforce their DECLARED granular
    # actions instead of documents.upload/documents.delete. Contributor reached
    # those routes via upload/delete before, so it gains the granular actions to
    # avoid a capability regression -- mirror the migration verbatim here.
    "folders.create", "folders.read", "folders.update", "folders.delete",
    "documents.metadata.update",
    "chat.use",
]


async def assign_contributor_role(
    session: AsyncSession, *users: User, name: str | None = None
) -> RoleTemplate:
    """Give each `users` account the migration-equivalent "Contributor" role
    (upload/delete/chat). Mirrors what the RBAC-04 forward migration does for
    every existing role="user" account; use it for any plain test user that
    should behave like a normal contributor now that the fallback default is
    read-only."""
    template = RoleTemplate(
        name=name or f"Contributor-{uuid4()}", permissions=list(CONTRIBUTOR_PERMISSIONS)
    )
    session.add(template)
    await session.flush()
    for user in users:
        user.custom_role_id = template.id
        session.add(user)
    await session.flush()
    return template


# --- ambient KEK, set at IMPORT time (deliberately not a fixture) ------------
# Settings.kek_file defaults to ./data/ragz_kek, so anything constructing
# Settings() reads whatever KEK happens to be on the machine. On a developer box
# the app has usually been bootstrapped so that file exists; on a CI runner it
# does not. That is why the isolation job failed while the same tests passed
# locally: they were reading a developer artifact.
#
# A fixture is too late. tests/modules/auth/test_service.py evaluates
# `SETTINGS = Settings(_env_file=None)` at MODULE level, which happens during
# collection, before any fixture runs. conftest is imported before test modules,
# so setting the variable here covers those module-level constants too.
#
# A test that needs its own KEK still passes kek_file= explicitly.
_KEK_TMPDIR = tempfile.mkdtemp(prefix="ragz-test-kek-")
_AMBIENT_KEK = str(Path(_KEK_TMPDIR) / "kek")
ensure_kek(_AMBIENT_KEK)
os.environ["RAGZ_KEK_FILE"] = _AMBIENT_KEK
atexit.register(lambda: shutil.rmtree(_KEK_TMPDIR, ignore_errors=True))


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as r:
        yield f"redis://{r.get_container_host_ip()}:{r.get_exposed_port(6379)}/0"


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    r = Redis.from_url(redis_url)
    await r.flushdb()  # rate-limit counters must not leak across tests
    yield r
    await r.aclose()


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    with QdrantContainer("qdrant/qdrant:v1.18.0") as q:
        yield f"http://{q.get_container_host_ip()}:{q.get_exposed_port(6333)}"


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_qdrant.cache_clear()  # also drops the client's httpx pool between event loops
    get_dense_embedder.cache_clear()


@pytest.fixture(scope="session")
def minio_config() -> Iterator[dict[str, str]]:
    with MinioContainer() as m:
        cfg = m.get_config()
        yield {
            "endpoint": f"http://{cfg['endpoint']}",
            "access_key": cfg["access_key"],
            "secret_key": cfg["secret_key"],
        }


@pytest.fixture
async def storage(minio_config: dict[str, str]) -> ObjectStorage:
    s = ObjectStorage(
        endpoint_url=minio_config["endpoint"],
        access_key=minio_config["access_key"],
        secret_key=minio_config["secret_key"],
        bucket="ragz-test",
    )
    await s.ensure_bucket()
    return s


@pytest.fixture
def pristine_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip every RAGZ_* variable so Settings() shows its CLASS defaults.

    stack_env is autouse, which is what stops tests silently reading the
    developer's dev stack -- but it also means the ambient environment is never
    empty. A test asserting "the default qdrant_url is localhost:56333" must
    therefore opt out explicitly, rather than depending on the environment
    happening to be unset, which is how it passed before.
    """
    for key in [k for k in os.environ if k.startswith("RAGZ_")]:
        monkeypatch.delenv(key, raising=False)
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture(autouse=True)
def stack_env(
    pg_url: str,
    redis_url: str,
    qdrant_url: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point ambient settings at the test containers; dense backend = deterministic
    hash (no TEI, no model downloads).

    AUTOUSE, because opt-in kept failing open. Any test that forgot to request
    it silently read the DEVELOPER'S dev stack -- Redis on :56379 and Qdrant on
    :56333, the ports deploy/compose.yaml publishes -- so the suite passed on a
    machine running `docker compose up` and failed on a CI runner. That is how
    19 tests could be green locally and red in CI. Opt-in isolation is only as
    good as the last person who remembered to opt in.
    """
    monkeypatch.setenv("RAGZ_DATABASE_URL", pg_url)
    # Redis was the one backing service NOT redirected here. Settings.redis_url
    # defaults to redis://localhost:56379/0 -- the port deploy/compose.yaml
    # publishes -- so any code resolving Redis from settings (rather than from
    # the injected client) talked to the DEVELOPER'S dev stack. That container
    # is running on a developer box and absent on a CI runner, which is why the
    # bots and attachment tests passed locally and failed in CI.
    monkeypatch.setenv("RAGZ_REDIS_URL", redis_url)
    # Celery is the reason the env var alone is not enough. build_celery() reads
    # get_settings() when ragz.worker.celery_app is IMPORTED, so its broker and
    # result backend are already bound to the ambient redis_url before any
    # fixture runs -- pointing at the dev stack's :56379. Rewriting the live
    # conf is what actually redirects it.
    from ragz.worker.celery_app import celery_app

    monkeypatch.setitem(celery_app.conf, "broker_url", redis_url)
    monkeypatch.setitem(celery_app.conf, "result_backend", redis_url)
    monkeypatch.setenv("RAGZ_QDRANT_URL", qdrant_url)
    monkeypatch.setenv("RAGZ_MINIO_ENDPOINT", minio_config["endpoint"])
    monkeypatch.setenv("RAGZ_MINIO_ACCESS_KEY", minio_config["access_key"])
    monkeypatch.setenv("RAGZ_MINIO_SECRET_KEY", minio_config["secret_key"])
    monkeypatch.setenv("RAGZ_MINIO_BUCKET", "ragz-test")
    monkeypatch.setenv("RAGZ_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RAGZ_RERANK_BACKEND", "lexical")
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
async def qdrant_collection(stack_env: None) -> None:
    """Fresh collection per test (the Qdrant container is session-scoped)."""
    from ragz.modules.retrieval.client import COLLECTION
    from ragz.modules.retrieval.service import ensure_collection

    client = get_qdrant()
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    await ensure_collection(COLLECTION, get_settings().embedding_dim)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = build_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Mirror migration d1e8f4a2b6c3's seed INSERT: the plain schema built
        # via create_all() never runs real Alembic migrations, so the
        # bootstrap "local embedding model" row (LOCAL_EMBEDDING_MODEL_ID)
        # that Workspace.embedding_model_id's ORM-side default points at
        # would otherwise never exist here, and every Workspace(...) insert
        # would fail its FK constraint. Same row shape, same fixed id.
        await conn.execute(
            sa.text(
                """
                INSERT INTO models (
                    id, created_at, litellm_model_name, display_name, provider_kind,
                    enabled, sync_status, tools_unreliable, is_utility,
                    supports_reasoning, default_reasoning_effort, supports_vision,
                    modality, dimension, collection_name
                ) VALUES (
                    :id, now(), 'local-embeddings', 'Local Embeddings (bge-m3)', 'tei',
                    true, 'synced', false, false,
                    false, 'off', false,
                    'embedding', :dimension, 'chunks_bge_m3'
                )
                """
            ).bindparams(
                sa.bindparam("id", value=LOCAL_EMBEDDING_MODEL_ID, type_=sa.Uuid()),
                sa.bindparam("dimension", value=get_settings().embedding_dim, type_=sa.Integer()),
            )
        )
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture(scope="session")
def kek_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("kek") / "ragz_kek"
    ensure_kek(str(path))
    return str(path)


@pytest.fixture
def test_settings(kek_file: str) -> Settings:
    # RAGZ-PUB-05: refresh-cookie Secure is now unconditional outside
    # environment == "test" -- the ASGI test client talks plain http, so a
    # Secure cookie would silently drop from its jar without this.
    return Settings(_env_file=None, kek_file=kek_file, environment="test")


def _stub_litellm_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/model/info":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(200, json={})


@pytest.fixture
async def client(
    engine: AsyncEngine, redis_client: Redis, test_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        session_factory=build_session_factory(engine),
        redis_client=redis_client,
        litellm_transport=httpx.MockTransport(_stub_litellm_handler),
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def seeded_user(session: AsyncSession) -> User:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id, email="a@acme.com", password_hash=hash_password("pw123456"), role="admin"
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def seeded_superadmin(session: AsyncSession) -> User:
    org = Organization(name="Platform")
    session.add(org)
    await session.flush()
    user = User(
        # NOTE: brief used "root@platform.test", but pydantic EmailStr (email_validator)
        # rejects the ".test" TLD as a reserved/special-use domain (RFC 2606) -- ".example"
        # is the RFC-2606 reserved domain that email_validator does NOT block.
        org_id=org.id, email="root@platform.example",
        password_hash=hash_password("pw123456"), role="superadmin",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def seeded_chat(session: AsyncSession, seeded_user: User) -> Chat:
    ws = Workspace(org_id=seeded_user.org_id, name="W")
    session.add(ws)
    await session.flush()
    chat = Chat(org_id=seeded_user.org_id, workspace_id=ws.id, user_id=seeded_user.id)
    session.add(chat)
    await session.commit()
    return chat


@pytest.fixture
async def user_headers(client: httpx.AsyncClient, seeded_user: User) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"email": seeded_user.email, "password": "pw123456"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def superadmin_headers(client: httpx.AsyncClient, seeded_superadmin: User) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"email": seeded_superadmin.email, "password": "pw123456"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class FakeStreamer:
    def __init__(self, deltas: list[str] | None = None) -> None:
        self.deltas = deltas if deltas is not None else ["Revenue was 12M ", "[1]."]
        self.calls: list[dict[str, object]] = []

    async def stream(  # type: ignore[no-untyped-def]
        self, *, model: str, messages: list[dict[str, str]], reasoning_effort: str | None = None
    ):
        self.calls.append(
            {"model": model, "messages": messages, "reasoning_effort": reasoning_effort}
        )
        for d in self.deltas:
            yield LLMDelta(d)
        yield LLMUsage(prompt_tokens=42, completion_tokens=7)


class FakeCompleter:
    """Scriptable LLMCompleter: pops one LLMCompletion per call; a dry script
    answers (usage 3/1) so loops always terminate in tests."""

    def __init__(self, script: list[LLMCompletion] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict[str, object]] = []

    async def complete(self, *, model, messages, tools=None, reasoning_effort=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {"model": model, "messages": messages, "tools": tools,
             "reasoning_effort": reasoning_effort}
        )
        if self.script:
            return self.script.pop(0)
        return LLMCompletion(
            text='{"action": "answer"}', tool_calls=[],
            usage=LLMUsage(prompt_tokens=3, completion_tokens=1),
        )


class FakeChunkReader:
    """Scriptable ChunkReader: document_id -> chunks (pinned path) and
    chunk_ref string -> chunk (backfill path). Records backfill calls."""

    def __init__(self) -> None:
        self.document_chunks: dict[UUID, list[RetrievedChunk]] = {}
        self.chunks_by_ref: dict[str, RetrievedChunk] = {}
        self.ref_calls: list[list[str]] = []

    async def list_document_chunks(  # type: ignore[no-untyped-def]
        self, ctx, workspace_id, document_id, *, collection_name
    ):
        return list(self.document_chunks.get(document_id, []))

    async def get_chunks_by_refs(  # type: ignore[no-untyped-def]
        self, ctx, workspace_id, refs, *, collection_name
    ):
        self.ref_calls.append(list(refs))
        return [self.chunks_by_ref[r] for r in refs if r in self.chunks_by_ref]


class FakeWebSearcher:
    """Scriptable WebSearcher (Task 11): default script returns one ISO 45001
    hit so a scripted {"action":"web_search",...} planner step always finds
    something to cite."""

    def __init__(
        self, results: list[WebResult] | None = None, *, billable: bool = False
    ) -> None:
        self.results = results if results is not None else [
            WebResult(title="ISO 45001 overview", url="https://example.test/iso",
                      snippet="ISO 45001 is an OHS standard."),
        ]
        self.queries: list[str] = []
        # Cost reporting: mirrors the real searchers' `billable` flag so tests
        # can exercise both the metered (Tavily) and free (DuckDuckGo) paths.
        self.billable = billable

    async def __call__(self, session, query):  # type: ignore[no-untyped-def]
        self.queries.append(query)
        return list(self.results)


class FakeRetriever:
    def __init__(self, document_id: UUID, no_answer: bool = False) -> None:
        self.document_id = document_id
        self.no_answer = no_answer
        self.chunks: list[RetrievedChunk] | None = None  # tests may script these
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self, session, ctx, workspace_id, query, top_k=None, metadata_clauses=None  # type: ignore[no-untyped-def]
    ) -> RetrievalResult:
        self.calls.append({"query": query, "metadata_clauses": metadata_clauses})
        if self.chunks is not None:
            return RetrievalResult(no_answer=self.no_answer, chunks=list(self.chunks))
        chunks = [
            RetrievedChunk(document_id=self.document_id, page=3, chunk_index=0,
                           text="Revenue was 12M.", score=0.91),
            RetrievedChunk(document_id=self.document_id, page=5, chunk_index=2,
                           text="Costs were 4M.", score=0.55),
        ]
        return RetrievalResult(no_answer=self.no_answer, chunks=chunks)


@pytest.fixture
async def utility_model(session: AsyncSession) -> Model:
    """A superadmin-designated utility model (Task 1, D5): Auditor/escalation/
    enrichment tests that need modules.models.utility.get_utility_model to
    resolve something use this rather than hand-rolling one per test file."""
    model = Model(
        litellm_model_name="utility-model", display_name="Utility",
        provider_kind="ollama", is_utility=True, enabled=True,
    )
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
async def chat_env(
    session: AsyncSession, seeded_user: User
) -> dict[str, object]:
    """Workspace + membership + one indexed document for chat tests."""
    ws = Workspace(org_id=seeded_user.org_id, name="ChatWS")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=seeded_user.id))
    doc = Document(org_id=seeded_user.org_id, workspace_id=ws.id,
                   filename="report.pdf", mime="application/pdf", size_bytes=10,
                   content_hash="h", status="indexed", storage_key="k",
                   created_by=seeded_user.id, lineage_id=uuid4())
    session.add(doc)
    await session.commit()
    return {"workspace": ws, "document": doc}
