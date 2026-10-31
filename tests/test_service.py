import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from health_api.core.checkpoint import CheckpointStore
from health_api.core.models import Severity
from health_api.core.monitor import Monitor
from health_api.service import MonitorService
from health_api.settings import Settings


def test_service_scan_updates_snapshot(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("2026-10-08 09:00:00 INFO Started\n", encoding="utf-8")
    state_file = tmp_path / "state.json"

    settings = Settings(root=log_dir, state_file=state_file, interval=10.0)
    checkpoint_store = CheckpointStore(state_file)
    monitor = Monitor(settings)
    service = MonitorService(settings, monitor, checkpoint_store)

    assert service.snapshot is None
    assert service.last_success_at is None
    ready, reason = service.is_ready()
    assert ready is False
    assert "pending" in reason

    report = service.scan()
    assert report.total_servers == 1
    assert service.snapshot == report
    assert service.last_success_at is not None
    assert service.consecutive_failures == 0
    assert state_file.exists()

    ready, reason = service.is_ready()
    assert ready is True
    assert reason == "ready"


def test_service_is_ready_stale(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    state_file = tmp_path / "state.json"

    settings = Settings(root=log_dir, state_file=state_file, interval=1.0)
    checkpoint_store = CheckpointStore(state_file)
    monitor = Monitor(settings)
    service = MonitorService(settings, monitor, checkpoint_store)

    service.scan()
    ready, _ = service.is_ready()
    assert ready is True

    # Simulate staleness by backdating last_success_at
    service._last_success_at = datetime.now(UTC) - timedelta(seconds=5)
    ready, reason = service.is_ready()
    assert ready is False
    assert "stale" in reason


def test_service_run_loop_stop(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("2026-10-08 09:00:00 INFO Start\n", encoding="utf-8")
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=log_dir, state_file=state_file, interval=0.1, min_severity=Severity.ERROR
    )
    checkpoint_store = CheckpointStore(state_file)
    monitor = Monitor(settings)
    service = MonitorService(settings, monitor, checkpoint_store)

    async def _test() -> None:
        stop_event = asyncio.Event()
        task = asyncio.create_task(service.run_loop(stop_event))
        await asyncio.sleep(0.25)
        stop_event.set()
        await task
        assert service.snapshot is not None

    asyncio.run(_test())

