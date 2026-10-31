"""Administrative write endpoints for manual scanning and fault resolution."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, status

from health_api.deps import get_app_settings, get_service, require_api_key
from health_api.schemas import (
    AffectedServerOut,
    FaultOut,
    ReportOut,
    ServerDetailOut,
    sanitize_message,
)
from health_api.service import (
    FaultNotActiveError,
    FaultNotFoundError,
    LogNotWritableError,
    MonitorService,
    ServerNotFoundError,
)
from health_api.settings import Settings

router = APIRouter(tags=["admin"], dependencies=[Depends(require_api_key)])

PATH_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


@router.post("/scan", response_model=ReportOut)
def trigger_scan(
    service: MonitorService = Depends(get_service),
    settings: Settings = Depends(get_app_settings),
) -> ReportOut:
    """Trigger an immediate scan cycle and return the updated report."""
    report = service.scan()
    now = datetime.now(UTC)

    affected: list[AffectedServerOut] = []
    for s in report.statuses:
        if not s.is_faulty:
            continue
        sorted_faults = sorted(
            s.active_faults,
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
        highest_sev = s.highest_severity.name if s.highest_severity else None
        affected.append(
            AffectedServerOut(
                server=s.relative_path,
                env=s.env,
                name=s.name,
                highest_severity=highest_sev,
                active_faults=fault_models,
            )
        )

    severity_order = {"CRIT": 4, "ERROR": 3, "WARN": 2, "INFO": 1}
    affected.sort(
        key=lambda s: (
            -severity_order.get(s.highest_severity or "", 0),
            s.server,
        )
    )

    return ReportOut(
        generated_at=now,
        last_scan_at=report.scanned_at,
        min_severity=settings.min_severity.name,
        total_servers=report.total_servers,
        faulty_servers=report.faulty_servers,
        affected_servers=affected,
    )


@router.post(
    "/servers/{env}/{server}/faults/{fault_id}/resolve",
    response_model=ServerDetailOut,
)
def resolve_fault(
    env: str = Path(..., pattern=PATH_PATTERN, description="Environment name"),
    server: str = Path(..., pattern=PATH_PATTERN, description="Server name or filename"),
    fault_id: str = Path(..., pattern=PATH_PATTERN, description="Fault ID to resolve"),
    service: MonitorService = Depends(get_service),
) -> ServerDetailOut:
    """Resolve an active fault by writing to the server log and rescanning."""
    try:
        updated = service.resolve_fault(env=env, server=server, fault_id=fault_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FaultNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FaultNotActiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"fault_not_active: {exc}",
        ) from exc
    except LogNotWritableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"log_not_writable: {exc}",
        ) from exc

    sorted_faults = sorted(
        updated.active_faults,
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
        server=updated.relative_path,
        env=updated.env,
        name=updated.name,
        is_faulty=updated.is_faulty,
        highest_severity=updated.highest_severity.name if updated.highest_severity else None,
        readable=updated.readable,
        error=updated.error,
        active_faults=fault_models,
    )
