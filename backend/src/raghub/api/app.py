import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from raghub.api.routes.admin_audit import router as admin_audit_router
from raghub.api.routes.admin_secrets import router as admin_secrets_router
from raghub.api.routes.admin_sso import router as admin_sso_router
from raghub.api.routes.auth import router as auth_router
from raghub.api.routes.chats import router as chats_router
from raghub.api.routes.documents import router as documents_router
from raghub.api.routes.groups import router as groups_router
from raghub.api.routes.health import router as health_router
from raghub.api.routes.models import router as models_router
from raghub.api.routes.oidc import router as oidc_router
from raghub.api.routes.search import router as search_router
from raghub.api.routes.usage import router as usage_router
from raghub.api.routes.users import router as users_router
from raghub.api.routes.workspaces import router as workspaces_router
from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory
from raghub.core.errors import RagHubError
from raghub.core.logging import configure_logging
from raghub.modules.chat.llm import LLMStreamer
from raghub.modules.chat.prompting import warm_token_encoder
from raghub.modules.chat.service import ChunkReader, Retriever
from raghub.modules.models.sync import sync_models_to_litellm
from raghub.modules.retrieval.service import RetrievalChunkReader, retrieve


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup replay heals proxy restarts/volume wipes (SEC-4 pattern). A failed
    startup sync is only a warning: the next registry CRUD or restart retries."""
    try:
        async with app.state.session_factory() as session:
            deployed = await sync_models_to_litellm(
                session, get_settings(), transport=app.state.litellm_transport
            )
        structlog.get_logger().info("litellm_startup_sync", deployed=deployed)
    except Exception as exc:  # noqa: BLE001 - startup must not die if the proxy is down
        structlog.get_logger().warning("litellm_startup_sync_failed", error=str(exc))
    # Off the request path: primes tiktoken's encoding cache (a blocking
    # network download on a cold cache) so the first chat turn never pays it.
    # warm_token_encoder never raises - a failed warmup just latches the same
    # char-estimate fallback a real request would hit anyway.
    await asyncio.to_thread(warm_token_encoder)
    yield


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Redis | None = None,
    litellm_transport: httpx.AsyncBaseTransport | None = None,
    retriever: Retriever | None = None,
    llm_streamer: LLMStreamer | None = None,
    chunk_reader: ChunkReader | None = None,
    oidc_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="RagHub", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=_lifespan
    )
    if session_factory is None:
        session_factory = build_session_factory(build_engine(get_settings().database_url))
    app.state.session_factory = session_factory
    if redis_client is None:
        redis_client = Redis.from_url(get_settings().redis_url)
    app.state.redis = redis_client
    app.state.litellm_transport = litellm_transport
    app.state.retriever = retriever if retriever is not None else retrieve
    app.state.llm_streamer = llm_streamer
    app.state.chunk_reader = chunk_reader if chunk_reader is not None else RetrievalChunkReader()
    app.state.oidc_transport = oidc_transport

    @app.exception_handler(RagHubError)
    async def handle_raghub_error(request: Request, exc: RagHubError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": exc.title,
                "status": exc.status_code,
                "detail": exc.detail,
            },
            media_type="application/problem+json",
        )

    logger = structlog.get_logger("raghub.api")

    def _problem(status: int, title: str, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"type": "about:blank", "title": title, "status": status, "detail": detail},
            media_type="application/problem+json",
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("integrity_error", method=request.method, path=request.url.path)
        return _problem(409, "Conflict", "resource conflicts with existing state")

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
            exc_info=exc,
        )
        return _problem(500, "Internal error", "an unexpected error occurred")

    app.include_router(health_router)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(oidc_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(groups_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(admin_secrets_router, prefix="/api/v1")
    app.include_router(admin_audit_router, prefix="/api/v1")
    app.include_router(admin_sso_router, prefix="/api/v1")
    app.include_router(models_router, prefix="/api/v1")
    app.include_router(chats_router, prefix="/api/v1")
    app.include_router(usage_router, prefix="/api/v1")
    return app
