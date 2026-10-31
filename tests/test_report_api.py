import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings


def test_report_endpoint_baseline_counts(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/api/v1/report")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_servers"] == 6
        assert data["faulty_servers"] == 3
        assert data["min_severity"] == "ERROR"

        affected = data["affected_servers"]
        assert len(affected) == 3

        # Highest severity should appear first
        assert affected[0]["highest_severity"] == "CRIT"

        affected_paths = {s["server"] for s in affected}
        assert affected_paths == {
            "dev/server-2.log",
            "prd/server-6.log",
            "staging/server-5.log",
        }

        # Check total active fault IDs
        fault_ids = {f["fault_id"] for s in affected for f in s["active_faults"]}
        assert fault_ids == {"F001", "F020", "F021", "F002"}


def test_report_message_sanitization(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "dev"
    log_dir.mkdir(parents=True)
    bad_line = "2026-10-08 09:00:00 ERROR [id=F999] Message with \x00\x08 control chars\n"
    (log_dir / "bad.log").write_bytes(bad_line.encode("utf-8"))
    state_file = tmp_path / "state.json"

    settings = Settings(root=tmp_path / "logs", state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/api/v1/report")
        assert resp.status_code == 200
        data = resp.json()
        fault = data["affected_servers"][0]["active_faults"][0]
        assert "\x00" not in fault["message"]
        assert "\x08" not in fault["message"]
        assert "Message with  control chars" in fault["message"]
