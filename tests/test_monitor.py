import shutil
from pathlib import Path

from health_api.core.models import Severity
from health_api.core.monitor import Monitor, MonitorSettings


def _copy_org(src_org: Path, dest_dir: Path) -> Path:
    target = dest_dir / "org"
    shutil.copytree(src_org, target)
    return target


def test_sample_org_error_severity(tmp_path: Path) -> None:
    work_org = _copy_org(Path("org"), tmp_path)
    settings = MonitorSettings(root=work_org, min_severity=Severity.ERROR)
    monitor = Monitor(settings)

    report = monitor.scan_once()

    assert report.total_servers == 6
    assert report.faulty_servers == 3

    statuses_by_path = {s.relative_path: s for s in report.statuses}
    assert not statuses_by_path["dev/server-1.log"].is_faulty
    assert statuses_by_path["dev/server-2.log"].is_faulty
    assert not statuses_by_path["dev/server-4.log"].is_faulty
    assert not statuses_by_path["prd/server-3.log"].is_faulty
    assert statuses_by_path["prd/server-6.log"].is_faulty
    assert statuses_by_path["staging/server-5.log"].is_faulty

    fault_ids = {f.fault_id for s in report.statuses for f in s.active_faults}
    assert fault_ids == {"F001", "F020", "F021", "F002"}
    assert report.duration_ms >= 0.0


def test_sample_org_warn_severity(tmp_path: Path) -> None:
    work_org = _copy_org(Path("org"), tmp_path)
    settings = MonitorSettings(root=work_org, min_severity=Severity.WARN)
    monitor = Monitor(settings)

    report = monitor.scan_once()

    assert report.total_servers == 6
    assert report.faulty_servers == 4  # dev/server-4 is now faulty
    assert report.discovery_errors == ()

    statuses_by_path = {s.relative_path: s for s in report.statuses}
    assert statuses_by_path["dev/server-4.log"].is_faulty

    all_faults = [f for s in report.statuses for f in s.active_faults]
    assert len(all_faults) == 5


def test_incremental_append(tmp_path: Path) -> None:
    log_dir = tmp_path / "test_env"
    log_dir.mkdir()
    log_file = log_dir / "app.log"
    log_file.write_text("2026-10-08 09:00:00 INFO Service started\n", encoding="utf-8")

    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)

    report1 = monitor.scan_once()
    assert report1.total_servers == 1
    assert report1.faulty_servers == 0

    # Append an ERROR line
    with open(log_file, "a", encoding="utf-8") as f:
        f.write("2026-10-08 09:02:00 ERROR [id=F100] Out of memory\n")

    report2 = monitor.scan_once()
    assert report2.faulty_servers == 1
    cp = monitor.state["test_env/app.log"]
    assert "F100" in cp.active_faults


def test_partial_line_held_back(tmp_path: Path) -> None:
    log_dir = tmp_path / "test_env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"

    # Write line without trailing newline
    log_file.write_bytes(b"2026-10-08 09:02:00 ERROR [id=F101] Partial line")

    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)

    report1 = monitor.scan_once()
    assert report1.faulty_servers == 0  # not yet applied

    # Append the newline
    with open(log_file, "ab") as f:
        f.write(b"\n")

    report2 = monitor.scan_once()
    assert report2.faulty_servers == 1


def test_truncation_resets_offset_keeps_faults(tmp_path: Path) -> None:
    log_dir = tmp_path / "test_env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"
    log_file.write_text(
        "2026-10-08 09:00:00 ERROR [id=F101] Persistent error\n"
        "2026-10-08 09:01:00 INFO some filler text\n",
        encoding="utf-8",
    )

    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)
    report1 = monitor.scan_once()
    assert report1.faulty_servers == 1

    # Truncate file
    log_file.write_text("2026-10-08 09:05:00 INFO Fresh start\n", encoding="utf-8")

    report2 = monitor.scan_once()
    assert report2.faulty_servers == 1  # F101 preserved
    assert "F101" in monitor.state["test_env/srv.log"].active_faults


def test_vanished_file_with_unresolved_fault(tmp_path: Path) -> None:
    log_dir = tmp_path / "test_env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"
    log_file.write_text("2026-10-08 09:00:00 ERROR [id=F101] Crash\n", encoding="utf-8")

    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)
    report1 = monitor.scan_once()
    assert report1.faulty_servers == 1

    # Remove file
    log_file.unlink()

    report2 = monitor.scan_once()
    assert report2.faulty_servers == 1
    vanished = next(s for s in report2.statuses if s.relative_path == "test_env/srv.log")
    assert vanished.is_faulty
    assert vanished.readable is False
    assert "missing but has unresolved" in (vanished.error or "")


def test_auto_derived_fault_id(tmp_path: Path) -> None:
    log_dir = tmp_path / "test_env"
    log_dir.mkdir()
    log_file = log_dir / "srv.log"
    log_file.write_text("2026-10-08 09:00:00 ERROR No id provided in this line\n", encoding="utf-8")

    settings = MonitorSettings(root=tmp_path, min_severity=Severity.ERROR)
    monitor = Monitor(settings)
    report = monitor.scan_once()

    assert report.faulty_servers == 1
    fault = report.statuses[0].active_faults[0]
    assert fault.fault_id.startswith("auto-")
