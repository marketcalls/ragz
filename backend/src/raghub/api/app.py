from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from raghub.api.routes.auth import router as auth_router
from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory
from raghub.core.errors import RagHubError
from raghub.core.logging import configure_logging


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    configure_logging()
    app = FastAPI(title="RagHub", docs_url="/api/docs", openapi_url="/api/openapi.json")
    if session_factory is None:
        session_factory = build_session_factory(build_engine(get_settings().database_url))
    app.state.session_factory = session_factory

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

    app.include_router(auth_router, prefix="/api/v1")
    return app
