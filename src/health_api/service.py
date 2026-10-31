"""Service layer coordinating thread-safe scanning, snapshots, and background loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from health_api.core.checkpoint import CheckpointStore
from health_api.core.models import ScanReport, ServerStatus
from health_api.core.monitor import Monitor
from health_api.settings import Settings

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Base error for service operations."""


class ServerNotFoundError(ServiceError):
    """Target server is not discovered."""


class FaultNotFoundError(ServiceError):
    """Target fault does not exist on server."""


class FaultNotActiveError(ServiceError):
    """Target fault was already resolved."""


class LogNotWritableError(ServiceError):
    """Cannot append resolution to server log file."""


def append_resolution(path: Path, fault_id: str, now: datetime | None = None) -> None:
    """Append INFO RESOLVED line to log file using a single binary write."""
    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    payload = f"{ts} INFO RESOLVED id={fault_id}\n".encode()

    try:
        prefix = b""
        if path.exists() and path.stat().st_size > 0:
            with open(path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    prefix = b"\n"

        with open(path, "ab") as fh:
            fh.write(prefix + payload)
            fh.flush()
    except OSError as exc:
        logger.error("cannot append resolution to %s: %s", path, exc)
        raise LogNotWritableError(f"cannot append resolution to log file: {exc}") from exc


class MonitorService:
    """Coordinates thread-safe log scanning, in-memory snapshots, and persistence."""

    def __init__(
        self,
        settings: Settings,
        monitor: Monitor,
        checkpoint_store: CheckpointStore,
    ) -> None:
        self._settings = settings
        self._monitor = monitor
        self._checkpoint_store = checkpoint_store
        self._lock = threading.Lock()
        self._snapshot: ScanReport | None = None
        self._last_success_at: datetime | None = None
        self._consecutive_failures: int = 0

    @property
    def snapshot(self) -> ScanReport | None:
        """Return the latest immutable scan snapshot."""
        return self._snapshot

    @property
    def last_success_at(self) -> datetime | None:
        return self._last_success_at

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def monitor(self) -> Monitor:
        return self._monitor

    def scan(self) -> ScanReport:
        """Run a synchronous scan under the lock, updating snapshot and checkpoint."""
        with self._lock:
            try:
                report = self._monitor.scan_once()
                self._snapshot = report
                self._checkpoint_store.save(self._monitor.state)
                self._last_success_at = report.scanned_at
                self._consecutive_failures = 0
                return report
            except Exception:
                self._consecutive_failures += 1
                logger.exception("scan cycle failed")
                raise

    def resolve_fault(self, env: str, server: str, fault_id: str) -> ServerStatus:
        """Resolve an active fault by writing to log file and triggering a rescan."""
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._monitor.scan_once()

            target_name = server.removesuffix(".log")
            matched: ServerStatus | None = None
            for s in self._snapshot.statuses:
                if s.env == env and (s.name == target_name or s.relative_path == f"{env}/{server}"):
                    matched = s
                    break

            if matched is None:
                raise ServerNotFoundError(f"server '{env}/{server}' not found")

            active_fault_ids = {f.fault_id for f in matched.active_faults}
            cp = self._monitor.state.get(matched.relative_path)
            if fault_id not in active_fault_ids:
                if cp is not None and fault_id in cp.seen_fault_ids:
                    raise FaultNotActiveError(f"fault '{fault_id}' is already resolved")
                raise FaultNotFoundError(f"fault '{fault_id}' not found on server '{env}/{server}'")

            log_path = self._settings.root / matched.relative_path
            append_resolution(log_path, fault_id)

            # Rescan to pick up resolution and update state
            report = self._monitor.scan_once()
            self._snapshot = report
            self._checkpoint_store.save(self._monitor.state)
            self._last_success_at = report.scanned_at
            self._consecutive_failures = 0

            for s in report.statuses:
                if s.relative_path == matched.relative_path:
                    return s

            return matched

    def is_ready(self) -> tuple[bool, str]:
        """Check if service has completed at least one fresh scan."""
        if self._snapshot is None or self._last_success_at is None:
            return False, "initial scan pending"

        max_age = 3.0 * self._settings.interval
        age = (datetime.now(UTC) - self._last_success_at).total_seconds()
        if age > max_age:
            return False, f"last successful scan is stale ({age:.1f}s old)"

        return True, "ready"

    def save_state(self) -> bool:
        """Persist state checkpoint under lock."""
        with self._lock:
            return self._checkpoint_store.save(self._monitor.state)

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        """Background loop executing recurring scans at configured intervals."""
        while not stop_event.is_set():
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._settings.interval)
                break

            try:
                await asyncio.to_thread(self.scan)
            except Exception:
                logger.warning("background scan iteration failed; continuing loop")
