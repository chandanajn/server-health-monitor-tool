from pathlib import Path

from health_api.core.models import Severity
from health_api.core.monitor import Monitor, MonitorSettings


def test_resolve_lifecycle(tmp_path: Path) -> None:
    log_dir = tmp_path / "env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"

    log_file.write_text("2026-10-08 09:00:00 ERROR [id=F001] Database down\n", encoding="utf-8")
    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)

    # 1. Fault opens
    report = monitor.scan_once()
    assert report.faulty_servers == 1
    assert "F001" in monitor.state["env/srv.log"].active_faults

    # 2. Append resolution line
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("2026-10-08 09:10:00 INFO RESOLVED id=F001\n")

    report = monitor.scan_once()
    assert report.faulty_servers == 0
    assert "F001" not in monitor.state["env/srv.log"].active_faults
    assert "F001" in monitor.state["env/srv.log"].seen_fault_ids

    # 3. Attempted replay or duplicate fault id is ignored
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("2026-10-08 09:15:00 ERROR [id=F001] Database down again\n")

    report = monitor.scan_once()
    assert report.faulty_servers == 0  # Still healthy; seen_fault_ids prevents reuse


def test_resolve_unknown_fault(tmp_path: Path) -> None:
    log_dir = tmp_path / "env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"

    log_file.write_text("2026-10-08 09:00:00 INFO RESOLVED id=F999\n", encoding="utf-8")
    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)

    report = monitor.scan_once()
    assert report.faulty_servers == 0
    assert report.total_servers == 1


def test_multiple_faults_selective_resolution(tmp_path: Path) -> None:
    log_dir = tmp_path / "env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"

    log_file.write_text(
        "2026-10-08 09:00:00 ERROR [id=F001] Disk full\n"
        "2026-10-08 09:02:00 CRIT [id=F002] Memory exhausted\n",
        encoding="utf-8",
    )
    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)

    report = monitor.scan_once()
    assert report.faulty_servers == 1
    cp = monitor.state["env/srv.log"]
    assert len(cp.active_faults) == 2

    # Resolve F001 only
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("2026-10-08 09:10:00 INFO RESOLVED id=F001\n")

    report = monitor.scan_once()
    assert report.faulty_servers == 1
    assert "F001" not in cp.active_faults
    assert "F002" in cp.active_faults
