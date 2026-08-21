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
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ragz.api.routes.admin_audit import router as admin_audit_router
from ragz.api.routes.admin_bots import router as admin_bots_router
from ragz.api.routes.admin_email import router as admin_email_router
from ragz.api.routes.admin_feedback import router as admin_feedback_router
from ragz.api.routes.admin_roles import router as admin_roles_router
from ragz.api.routes.admin_secrets import router as admin_secrets_router
from ragz.api.routes.admin_sso import router as admin_sso_router
from ragz.api.routes.api_keys import router as api_keys_router
from ragz.api.routes.auth import router as auth_router
from ragz.api.routes.bots import router as bots_router
from ragz.api.routes.chats import router as chats_router
from ragz.api.routes.client_errors import router as client_errors_router
from ragz.api.routes.documents import router as documents_router
from ragz.api.routes.evals import router as evals_router
from ragz.api.routes.external import router as external_router
from ragz.api.routes.groups import router as groups_router
from ragz.api.routes.health import router as health_router
from ragz.api.routes.me import router as me_router
from ragz.api.routes.media import router as media_router
from ragz.api.routes.models import router as models_router
from ragz.api.routes.oidc import router as oidc_router
from ragz.api.routes.reports import router as reports_router
from ragz.api.routes.search import router as search_router
from ragz.api.routes.settings import router as settings_router
from ragz.api.routes.superadmin_ops import router as superadmin_ops_router
from ragz.api.routes.usage import router as usage_router
from ragz.api.routes.users import router as users_router
from ragz.api.routes.workspaces import router as workspaces_router
from ragz.api.security_middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    body_size_ceiling_bytes,
    trusted_hosts_for,
)
from ragz.core.config import Settings, get_settings
from ragz.core.db import build_engine, build_session_factory, dispose_loop_engine
from ragz.core.errors import RagzError
from ragz.core.logging import configure_logging
from ragz.core.middleware import (
    MetricsMiddleware,
    RequestIDMiddleware,
    TracingMiddleware,
)
from ragz.core.tracing import configure_tracing
from ragz.modules.chat.llm import LLMCompleter, LLMStreamer
from ragz.modules.chat.prompting import warm_token_encoder
from ragz.modules.chat.service import ChunkReader, Retriever
from ragz.modules.chat.web import WebSearcher
from ragz.modules.models.sync import sync_models_to_litellm
from ragz.modules.retrieval.service import RetrievalChunkReader, retrieve


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
    # ADR-0006: routes nudge the outbox dispatcher, which opens a session via
    # ingest._session, which caches an engine for THIS loop. The worker disposes
    # its equivalent on worker_process_shutdown; the API had no teardown at all,
    # so that pool outlived the app.
    #
    # Only the loop-cached engine is disposed. app.state.session_factory may be
    # supplied by the caller -- the test suite shares one factory across every
    # app it builds -- so disposing that would tear down a pool this app never
    # created and other callers still hold.
    await dispose_loop_engine()


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    redis_client: Redis | None = None,
    litellm_transport: httpx.AsyncBaseTransport | None = None,
    retriever: Retriever | None = None,
    llm_streamer: LLMStreamer | None = None,
    chunk_reader: ChunkReader | None = None,
    oidc_transport: httpx.AsyncBaseTransport | None = None,
    llm_completer: LLMCompleter | None = None,
    web_searcher: WebSearcher | None = None,
    bot_outbound_transport: httpx.AsyncBaseTransport | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    configure_logging()
    if settings is None:
        settings = get_settings()
    # RAGZ-PUB-09: Swagger UI + the raw OpenAPI schema are a reconnaissance
    # gift to an attacker (routes, param shapes, auth scheme) -- gate both
    # off in production while leaving dev/test/staging exactly as before.
    docs_enabled = settings.environment != "production"
    app = FastAPI(
        title="Ragz",
        docs_url="/api/docs" if docs_enabled else None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )
    if session_factory is None:
        session_factory = build_session_factory(
            build_engine(
                settings.database_url,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_pool_timeout_seconds,
            )
        )
    app.state.session_factory = session_factory
    if redis_client is None:
        redis_client = Redis.from_url(
            settings.redis_url, max_connections=settings.redis_max_connections
        )
    app.state.redis = redis_client
    app.state.litellm_transport = litellm_transport
    app.state.retriever = retriever if retriever is not None else retrieve
    app.state.llm_streamer = llm_streamer
    app.state.chunk_reader = chunk_reader if chunk_reader is not None else RetrievalChunkReader()
    app.state.oidc_transport = oidc_transport
    app.state.llm_completer = llm_completer
    # None here means "construct a real TavilySearcher at the route" (chats.py) --
    # tests inject a fake directly; production leaves this None.
    app.state.web_searcher = web_searcher
    # None here means "make real outbound HTTP calls to Telegram/Slack/
    # Discord" -- tests inject a MockTransport so bot replies never hit the
    # network (mirrors litellm_transport/oidc_transport).
    app.state.bot_outbound_transport = bot_outbound_transport

    @app.exception_handler(RagzError)
    async def handle_ragz_error(request: Request, exc: RagzError) -> JSONResponse:
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

    logger = structlog.get_logger("ragz.api")

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
    app.include_router(evals_router, prefix="/api/v1")
    app.include_router(groups_router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    app.include_router(me_router, prefix="/api/v1")
    app.include_router(media_router, prefix="/api/v1")
    app.include_router(admin_secrets_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(admin_email_router, prefix="/api/v1")
    app.include_router(admin_audit_router, prefix="/api/v1")
    app.include_router(admin_bots_router, prefix="/api/v1")
    app.include_router(api_keys_router, prefix="/api/v1")
    app.include_router(admin_feedback_router, prefix="/api/v1")
    app.include_router(admin_sso_router, prefix="/api/v1")
    app.include_router(admin_roles_router, prefix="/api/v1")
    app.include_router(models_router, prefix="/api/v1")
    app.include_router(chats_router, prefix="/api/v1")
    app.include_router(usage_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")
    app.include_router(client_errors_router, prefix="/api/v1")
    app.include_router(superadmin_ops_router, prefix="/api/v1")
    app.include_router(external_router, prefix="/external/v1")
    app.include_router(bots_router, prefix="/external/bots")

    # Middleware order: Starlette runs the LAST-added `add_middleware` call
    # OUTERMOST (first on the request, last on the response). RequestID and
    # SecurityHeaders are added first here so TrustedHost + BodySizeLimit end
    # up outermost -- rejecting a bad Host or an oversized body before any
    # inner work (request-id binding, route handling) ever runs. Effective
    # stack, outer -> inner: TrustedHost -> BodySizeLimit -> SecurityHeaders
    # -> RequestID -> (Starlette's own exception handling) -> routes.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware, hsts=(settings.environment == "production")
    )
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=body_size_ceiling_bytes(settings.max_upload_mb)
    )
    # Added last => OUTERMOST, deliberately outside TrustedHost and
    # BodySizeLimit. Those two reject requests before any route runs, and a
    # rejected request is exactly the kind of thing an operator wants on a
    # graph -- inside them, a flood of oversized uploads or bad Host headers
    # would be invisible. It observes only; nothing downstream depends on it,
    # so the outermost position costs no early-rejection benefit.
    # It still labels by route template: scope["route"] is read after the inner
    # app returns, and routing mutates the same scope dict this sees.
    app.add_middleware(MetricsMiddleware)
    # Inside metrics: metrics must see every request, including ones an outer
    # middleware rejects, whereas a span for a request that never reached
    # routing carries no useful detail. Tracing is a no-op unless
    # RAGZ_OTEL_ENDPOINT is set, so this costs a tracer lookup when off.
    if settings.otel_endpoint:
        configure_tracing(
            endpoint=settings.otel_endpoint, service_name=settings.otel_service_name
        )
    app.add_middleware(TracingMiddleware)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts_for(settings.environment, settings.public_api_base_url),
    )

    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn, environment=settings.environment,
                traces_sample_rate=0.0, send_default_pii=False,
            )
        except ImportError:
            structlog.get_logger().warning("sentry_dsn set but sentry-sdk not installed")

    return app
