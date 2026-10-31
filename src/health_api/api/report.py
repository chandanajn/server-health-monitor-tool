"""Operational health summary report endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from health_api.deps import get_app_settings, get_service
from health_api.schemas import AffectedServerOut, FaultOut, ReportOut, sanitize_message
from health_api.service import MonitorService
from health_api.settings import Settings

router = APIRouter(tags=["report"])


@router.get("/report", response_model=ReportOut)
def get_report(
    service: MonitorService = Depends(get_service),
    settings: Settings = Depends(get_app_settings),
) -> ReportOut:
    """Return operational health overview and affected servers."""
    snapshot = service.snapshot
    now = datetime.now(UTC)

    if snapshot is None:
        return ReportOut(
            generated_at=now,
            last_scan_at=None,
            min_severity=settings.min_severity.name,
            total_servers=0,
            faulty_servers=0,
            affected_servers=[],
        )

    affected: list[AffectedServerOut] = []
    for s in snapshot.statuses:
        if not s.is_faulty:
            continue
        # Order faults by severity descending, then opened_at descending
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

    # Order affected servers by highest severity descending, then relative path
    severity_order = {"CRIT": 4, "ERROR": 3, "WARN": 2, "INFO": 1}
    affected.sort(
        key=lambda s: (
            -severity_order.get(s.highest_severity or "", 0),
            s.server,
        )
    )

    return ReportOut(
        generated_at=now,
        last_scan_at=snapshot.scanned_at,
        min_severity=settings.min_severity.name,
        total_servers=snapshot.total_servers,
        faulty_servers=snapshot.faulty_servers,
        affected_servers=affected,
    )
