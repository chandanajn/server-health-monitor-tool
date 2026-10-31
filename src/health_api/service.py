"""Service layer coordinating thread-safe scanning, snapshots, and background loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from datetime import UTC, datetime

from health_api.core.checkpoint import CheckpointStore
from health_api.core.models import ScanReport
from health_api.core.monitor import Monitor
from health_api.settings import Settings

logger = logging.getLogger(__name__)


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
