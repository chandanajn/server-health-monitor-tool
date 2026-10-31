"""FastAPI dependencies for services and settings."""

from __future__ import annotations

from fastapi import Request

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

