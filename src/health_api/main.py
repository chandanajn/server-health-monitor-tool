"""Application factory and lifespan management for the Health Monitor API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from health_api.api.admin import router as admin_router
from health_api.api.faults import router as faults_router
from health_api.api.health import router as health_router
from health_api.api.report import router as report_router
from health_api.api.servers import router as servers_router
from health_api.core.checkpoint import CheckpointStore
from health_api.core.monitor import Monitor
from health_api.logging_config import configure_logging
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

    app.include_router(health_router)
    app.include_router(report_router, prefix="/api/v1")
    app.include_router(servers_router, prefix="/api/v1")
    app.include_router(faults_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    return app
