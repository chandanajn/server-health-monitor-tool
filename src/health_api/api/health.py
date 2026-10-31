"""Liveness and readiness health probe endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from health_api.deps import get_service
from health_api.service import MonitorService

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(
    response: Response,
    service: MonitorService = Depends(get_service),
) -> dict[str, str]:
    """Readiness probe checking scan freshness and availability."""
    ready, reason = service.is_ready()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": reason}
    return {"status": "ready"}
