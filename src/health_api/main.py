"""Application factory and lifespan management for the Health Monitor API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi import status as http_status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from health_api.api.admin import router as admin_router
from health_api.api.faults import router as faults_router
from health_api.api.health import router as health_router
from health_api.api.report import router as report_router
from health_api.api.servers import router as servers_router
from health_api.core.checkpoint import CheckpointStore
from health_api.core.monitor import Monitor
from health_api.errors import make_error_response, register_error_handlers
from health_api.logging_config import configure_logging, request_id_ctx
from health_api.service import MonitorService
from health_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI application instance."""
    app_settings = settings or get_settings()
    configure_logging(level=app_settings.log_level, fmt=app_settings.log_format)

    checkpoint_store = CheckpointStore(app_settings.state_file)
    initial_state = checkpoint_store.load()
    monitor = Monitor(app_settings, state=initial_state)
    service = MonitorService(app_settings, monitor, checkpoint_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.service = service
        app.state.settings = app_settings

        # Initial scan before serving traffic
        try:
            await asyncio.to_thread(service.scan)
        except Exception:
            logger.exception("initial scan failed; service will start unready")

        stop_event = asyncio.Event()
        loop_task = asyncio.create_task(service.run_loop(stop_event))

        try:
            yield
        finally:
            stop_event.set()
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            service.save_state()

    docs_url = "/docs" if app_settings.enable_docs else None
    redoc_url = "/redoc" if app_settings.enable_docs else None
    openapi_url = "/openapi.json" if app_settings.enable_docs else None

    app = FastAPI(
        title="Server Health Monitor API",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.settings = app_settings

    register_error_handlers(app)

    @app.middleware("http")
    async def request_id_and_access_log_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming_id = request.headers.get("X-Request-ID")
        rid = (
            incoming_id.strip()
            if incoming_id and incoming_id.strip()
            else f"req-{uuid.uuid4().hex[:12]}"
        )

        token = request_id_ctx.set(rid)
        request.state.request_id = rid

        start = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000.0
            response.headers["X-Request-ID"] = rid
            logger.info(
                "%s %s -> %d (%.2fms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            return response
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.exception(
                "%s %s -> 500 (%.2fms) unhandled exception [request_id=%s]: %s",
                request.method,
                request.url.path,
                duration_ms,
                rid,
                exc,
            )
            return make_error_response(
                "internal_error",
                "An unexpected internal server error occurred",
                http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                request_id=rid,
            )
        finally:
            request_id_ctx.reset(token)

    if app_settings.enable_metrics:

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health_router)
    app.include_router(report_router, prefix="/api/v1")
    app.include_router(servers_router, prefix="/api/v1")
    app.include_router(faults_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    return app
