"""Server status query and detail endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from health_api.deps import get_service
from health_api.schemas import FaultOut, Page, ServerDetailOut, ServerOut, sanitize_message
from health_api.service import MonitorService

router = APIRouter(prefix="/servers", tags=["servers"])

PATH_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


@router.get("", response_model=Page[ServerOut])
def list_servers(
    env: str | None = Query(None, pattern=PATH_PATTERN, description="Filter by environment"),
    status_filter: Literal["faulty", "healthy"] | None = Query(
        None, alias="status", description="Filter by server status"
    ),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    service: MonitorService = Depends(get_service),
) -> Page[ServerOut]:
    """Return paginated list of monitored servers."""
    snapshot = service.snapshot
    if snapshot is None:
        return Page(items=[], total=0, limit=limit, offset=offset)

    filtered = snapshot.statuses
    if env is not None:
        filtered = tuple(s for s in filtered if s.env == env)

    if status_filter == "faulty":
        filtered = tuple(s for s in filtered if s.is_faulty)
    elif status_filter == "healthy":
        filtered = tuple(s for s in filtered if not s.is_faulty)

    total = len(filtered)
    page_items = filtered[offset : offset + limit]

    items = [
        ServerOut(
            server=s.relative_path,
            env=s.env,
            name=s.name,
            is_faulty=s.is_faulty,
            fault_count=len(s.active_faults),
            highest_severity=s.highest_severity.name if s.highest_severity else None,
            readable=s.readable,
        )
        for s in page_items
    ]

    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{env}/{server}", response_model=ServerDetailOut)
def get_server_detail(
    env: str = Path(..., pattern=PATH_PATTERN, description="Environment name"),
    server: str = Path(..., pattern=PATH_PATTERN, description="Server name or filename"),
    service: MonitorService = Depends(get_service),
) -> ServerDetailOut:
    """Return detailed status and active faults for one server."""
    snapshot = service.snapshot
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="server not found")

    target_name = server.removesuffix(".log")
    matched = None
    for s in snapshot.statuses:
        if s.env == env and (s.name == target_name or s.relative_path == f"{env}/{server}"):
            matched = s
            break

    if matched is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="server not found")

    sorted_faults = sorted(
        matched.active_faults,
        key=lambda f: (f.severity, f.opened_at),
        reverse=True,
    )
    fault_models = [
        FaultOut(
            fault_id=f.fault_id,
            severity=f.severity.name,
            since=f.opened_at,
            message=sanitize_message(f.message),
        )
        for f in sorted_faults
    ]

    return ServerDetailOut(
        server=matched.relative_path,
        env=matched.env,
        name=matched.name,
        is_faulty=matched.is_faulty,
        highest_severity=matched.highest_severity.name if matched.highest_severity else None,
        readable=matched.readable,
        error=matched.error,
        active_faults=fault_models,
    )
