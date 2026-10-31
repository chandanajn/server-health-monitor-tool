"""FastAPI dependencies for services, settings, and authentication."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from health_api.service import MonitorService
from health_api.settings import Settings


def get_service(request: Request) -> MonitorService:
    """Extract MonitorService instance from app state."""
    service: MonitorService = request.app.state.service
    return service


def get_app_settings(request: Request) -> Settings:
    """Extract application Settings instance from app state."""
    settings: Settings = request.app.state.settings
    return settings


def require_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Validate X-API-Key header in constant time; block writes if unset."""
    if settings.api_key is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="writes_disabled",
        )
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
        )
    if not hmac.compare_digest(x_api_key.encode("utf-8"), settings.api_key.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
        )
