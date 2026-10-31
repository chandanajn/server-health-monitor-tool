import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.core.models import LogEvent, Severity
from health_api.core.monitor import Monitor, MonitorSettings
from health_api.core.parser import parse_line
from health_api.main import create_app
from health_api.settings import Settings


def test_parser_and_monitor_crlf_logs(tmp_path: Path) -> None:
    # Test line parser with CRLF
    line = "2026-10-31 12:00:00 ERROR [id=F_CRLF] Windows line ending error\r\n"
    event = parse_line(line)
    assert isinstance(event, LogEvent)
    assert event.severity == Severity.ERROR
    assert event.fault_id == "F_CRLF"
    assert event.message == "Windows line ending error"

    # Test monitor reading CRLF log file
    log_dir = tmp_path / "logs" / "dev"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "server-crlf.log"
    content = (
        "2026-10-31 10:00:00 INFO System started\r\n"
        "2026-10-31 10:01:00 WARN High memory usage\r\n"
        "2026-10-31 10:02:00 ERROR [id=F_WIN1] Service crashed\r\n"
        "2026-10-31 10:03:00 INFO RESOLVED id=F_WIN1\r\n"
        "2026-10-31 10:04:00 CRIT [id=F_WIN2] Disk full\r\n"
    )
    log_file.write_bytes(content.encode("utf-8"))

    monitor = Monitor(MonitorSettings(root=tmp_path / "logs", min_severity=Severity.ERROR))
    report = monitor.scan_once()
    assert report.total_servers == 1
    assert report.faulty_servers == 1
    status = report.statuses[0]
    assert len(status.active_faults) == 1
    assert status.active_faults[0].fault_id == "F_WIN2"


def test_parser_and_monitor_utf8_bom(tmp_path: Path) -> None:
    # Test parser strips leading UTF-8 BOM
    line_with_bom = "\ufeff2026-10-31 12:00:00 ERROR [id=F_BOM] First line has BOM\n"
    event = parse_line(line_with_bom)
    assert isinstance(event, LogEvent)
    assert event.severity == Severity.ERROR
    assert event.fault_id == "F_BOM"

    # Test monitor reading file starting with UTF-8 BOM bytes
    log_dir = tmp_path / "logs" / "staging"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "server-bom.log"
    bom_bytes = b"\xef\xbb\xbf" + b"2026-10-31 09:00:00 ERROR [id=F_BOM_TEST] Error after BOM\n"
    log_file.write_bytes(bom_bytes)

    monitor = Monitor(MonitorSettings(root=tmp_path / "logs", min_severity=Severity.ERROR))
    report = monitor.scan_once()
    assert report.total_servers == 1
    assert report.faulty_servers == 1
    assert report.statuses[0].active_faults[0].fault_id == "F_BOM_TEST"


def test_invalid_bytes_in_logs_handled_gracefully(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "dev"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "server-corrupt-bytes.log"

    # Line contains invalid UTF-8 sequences mixed with a valid fault
    bad_bytes = (
        b"2026-10-31 08:00:00 INFO Normal prefix\n"
        b"\xff\xfe\x00\x80\x90 invalid byte sequence here\n"
        b"2026-10-31 08:05:00 ERROR [id=F_RECOVER] Valid fault after corrupted bytes\n"
    )
    log_file.write_bytes(bad_bytes)

    monitor = Monitor(MonitorSettings(root=tmp_path / "logs", min_severity=Severity.ERROR))
    report = monitor.scan_once()
    assert report.total_servers == 1
    assert report.faulty_servers == 1
    assert report.statuses[0].active_faults[0].fault_id == "F_RECOVER"


def test_empty_org_directory(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty_logs"
    empty_root.mkdir()
    state_file = tmp_path / "state.json"

    settings = Settings(root=empty_root, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/api/v1/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_servers"] == 0
        assert data["faulty_servers"] == 0
        assert len(data["affected_servers"]) == 0


def test_corrupt_checkpoint_at_startup_quarantines_and_recovers(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    # Write garbage directly to state file
    state_file.write_text("{this is complete corrupted json;!@#$%", encoding="utf-8")

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/api/v1/report")
        assert resp.status_code == 200
        # Service started up cleanly and scanned fresh
        assert resp.json()["total_servers"] == 6

    # Verify original corrupt file was quarantined
    quarantine_files = list(tmp_path.glob("state.json.corrupt*"))
    assert len(quarantine_files) >= 1


def test_monitored_root_removed_while_running(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file, api_key="test-api-key-16-chars")
    app = create_app(settings)

    with TestClient(app) as client:
        # Initial scan ok
        rep = client.get("/api/v1/report").json()
        assert rep["total_servers"] == 6

        # Delete monitored directory
        shutil.rmtree(work_org)

        # Rescan should handle directory removal without crashing, preserving active faults
        scan_resp = client.post("/api/v1/scan", headers={"X-API-Key": "test-api-key-16-chars"})
        assert scan_resp.status_code == 200
        data = scan_resp.json()
        assert data["faulty_servers"] == 3
        # Affected servers should be marked unreadable
        for s in data["affected_servers"]:
            srv_detail = client.get(f"/api/v1/servers/{s['env']}/{s['name']}").json()
            assert srv_detail["readable"] is False
            assert "file missing" in (srv_detail["error"] or "")


def test_large_number_of_small_files(tmp_path: Path) -> None:
    large_root = tmp_path / "many_logs"
    large_root.mkdir()

    # Create 50 environments with 10 server logs each = 500 files
    for e in range(50):
        env_dir = large_root / f"env-{e}"
        env_dir.mkdir()
        for s in range(10):
            fpath = env_dir / f"server-{s}.log"
            # 1 in 10 has a fault
            if s == 0:
                fpath.write_text(
                    f"2026-10-31 12:00:00 ERROR [id=F_ENV_{e}] error\n", encoding="utf-8"
                )
            else:
                fpath.write_text("2026-10-31 12:00:00 INFO server running ok\n", encoding="utf-8")

    monitor = Monitor(MonitorSettings(root=large_root, min_severity=Severity.ERROR))
    report = monitor.scan_once()
    assert report.total_servers == 500
    assert report.faulty_servers == 50
    assert len(report.discovery_errors) == 0
