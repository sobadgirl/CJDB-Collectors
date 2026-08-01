"""FastAPI application factory."""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request

from cjdb_collectors.routes import app_router
from cjdb_collectors.routes.errors import install_error_handlers


def _default_services() -> Any:
    from cjdb_collectors.config import load_settings
    from cjdb_collectors.db import migrate_database
    from cjdb_collectors.services import build_services

    settings = load_settings()
    migrate_database(settings)
    return build_services(settings=settings)


def create_app(*, services: Any | None = None) -> FastAPI:
    """Create an app; injected services make HTTP E2E tests deterministic."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "services"):
            app.state.services = _default_services()
        yield
        close = getattr(app.state.services, "close", None)
        if callable(close):
            close()

    app = FastAPI(
        title="超级对标 Connectors",
        version="0.1.0",
        description="本地优先的对标数据采集、转写和同步服务。",
        lifespan=lifespan,
    )
    if services is not None:
        app.state.services = services
    install_error_handlers(app)

    @app.get("/health/live", tags=["health"], include_in_schema=False)
    def live_alias() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], include_in_schema=False)
    def ready_alias(request: Request) -> Any:
        return request.app.state.services.health.ready()

    app.include_router(app_router)
    return app


app = create_app()
