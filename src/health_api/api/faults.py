"""Active fault query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from health_api.core.models import Severity
from health_api.deps import get_service
from health_api.schemas import FlatFaultOut, sanitize_message
from health_api.service import MonitorService

router = APIRouter(prefix="/faults", tags=["faults"])

PATH_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


@router.get("", response_model=list[FlatFaultOut])
def list_faults(
    env: str | None = Query(None, pattern=PATH_PATTERN, description="Filter by environment"),
    min_severity: str | None = Query(None, description="Minimum fault severity filter"),
    service: MonitorService = Depends(get_service),
) -> list[FlatFaultOut]:
    """Return flat list of all active faults across servers."""
    min_sev_enum: Severity | None = None
    if min_severity is not None:
        try:
            min_sev_enum = Severity.from_name(min_severity)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"invalid min_severity: {min_severity!r}",
            ) from None

    snapshot = service.snapshot
    if snapshot is None:
        return []

    faults: list[FlatFaultOut] = []
    for s in snapshot.statuses:
        if env is not None and s.env != env:
            continue
        for f in s.active_faults:
            if min_sev_enum is not None and f.severity < min_sev_enum:
                continue
            faults.append(
                FlatFaultOut(
                    server=s.relative_path,
                    env=s.env,
                    fault_id=f.fault_id,
                    severity=f.severity.name,
                    since=f.opened_at,
                    message=sanitize_message(f.message),
                )
            )

    severity_order = {"CRIT": 4, "ERROR": 3, "WARN": 2, "INFO": 1}
    faults.sort(
        key=lambda item: (
            -severity_order.get(item.severity, 0),
            item.server,
            item.fault_id,
        )
    )

    return faults
