from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.qdrant import QdrantContainer
from testcontainers.redis import RedisContainer

from raghub.api.app import create_app
from raghub.core.config import Settings, get_settings
from raghub.core.db import Base, build_engine, build_session_factory
from raghub.core.storage import ObjectStorage
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.chat.llm import LLMDelta, LLMUsage
from raghub.modules.documents.models import Document
from raghub.modules.retrieval.client import get_qdrant
from raghub.modules.retrieval.embeddings import get_dense_embedder
from raghub.modules.retrieval.rerank import get_reranker
from raghub.modules.retrieval.service import RetrievalResult, RetrievedChunk
from raghub.modules.secrets.crypto import ensure_kek
from raghub.modules.tenancy.models import Organization, Workspace, WorkspaceMember


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
    get_reranker.cache_clear()


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
        bucket="raghub-test",
    )
    await s.ensure_bucket()
    return s


@pytest.fixture
def stack_env(
    pg_url: str,
    qdrant_url: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point ambient settings at the test containers; dense backend = deterministic
    hash (no TEI, no model downloads)."""
    monkeypatch.setenv("RAGHUB_DATABASE_URL", pg_url)
    monkeypatch.setenv("RAGHUB_QDRANT_URL", qdrant_url)
    monkeypatch.setenv("RAGHUB_MINIO_ENDPOINT", minio_config["endpoint"])
    monkeypatch.setenv("RAGHUB_MINIO_ACCESS_KEY", minio_config["access_key"])
    monkeypatch.setenv("RAGHUB_MINIO_SECRET_KEY", minio_config["secret_key"])
    monkeypatch.setenv("RAGHUB_MINIO_BUCKET", "raghub-test")
    monkeypatch.setenv("RAGHUB_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("RAGHUB_RERANK_BACKEND", "lexical")
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
async def qdrant_collection(stack_env: None) -> None:
    """Fresh collection per test (the Qdrant container is session-scoped)."""
    from raghub.modules.retrieval.client import COLLECTION
    from raghub.modules.retrieval.service import ensure_collection

    client = get_qdrant()
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    await ensure_collection()


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    eng = build_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = build_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture
def kek_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("kek") / "raghub_kek"
    ensure_kek(str(path))
    return str(path)


@pytest.fixture
def test_settings(kek_file: str) -> Settings:
    return Settings(_env_file=None, kek_file=kek_file)


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


class FakeStreamer:
    def __init__(self, deltas: list[str] | None = None) -> None:
        self.deltas = deltas if deltas is not None else ["Revenue was 12M ", "[1]."]
        self.calls: list[dict[str, object]] = []

    async def stream(self, *, model: str, messages: list[dict[str, str]]):  # type: ignore[no-untyped-def]
        self.calls.append({"model": model, "messages": messages})
        for d in self.deltas:
            yield LLMDelta(d)
        yield LLMUsage(prompt_tokens=42, completion_tokens=7)


class FakeRetriever:
    def __init__(self, document_id: UUID, no_answer: bool = False) -> None:
        self.document_id = document_id
        self.no_answer = no_answer

    async def __call__(
        self, session, ctx, workspace_id, query, top_k=8  # type: ignore[no-untyped-def]
    ) -> RetrievalResult:
        chunks = [
            RetrievedChunk(document_id=self.document_id, page=3, chunk_index=0,
                           text="Revenue was 12M.", score=0.91),
            RetrievedChunk(document_id=self.document_id, page=5, chunk_index=2,
                           text="Costs were 4M.", score=0.55),
        ]
        return RetrievalResult(no_answer=self.no_answer, chunks=chunks)


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
                   created_by=seeded_user.id)
    session.add(doc)
    await session.commit()
    return {"workspace": ws, "document": doc}
