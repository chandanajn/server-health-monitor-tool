import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings

API_KEY = "e2e-secret-test-key-16-chars"
AUTH_HEADERS = {"X-API-Key": API_KEY}


def test_e2e_live_append_and_resolve_flow(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=work_org,
        state_file=state_file,
        api_key=API_KEY,
        enable_metrics=True,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Initial report: baseline 6 servers, 3 faulty
        rep0 = client.get("/api/v1/report").json()
        assert rep0["total_servers"] == 6
        assert rep0["faulty_servers"] == 3

        # 2. Append new fault to previously healthy dev/server-1.log
        server1_log = work_org / "dev" / "server-1.log"
        with open(server1_log, "a", encoding="utf-8") as f:
            f.write("2026-10-31 15:00:00 ERROR [id=F101] critical database timeout\n")

        # 3. Trigger manual scan via API
        scan_resp = client.post("/api/v1/scan", headers=AUTH_HEADERS)
        assert scan_resp.status_code == 200
        rep1 = scan_resp.json()
        assert rep1["total_servers"] == 6
        assert rep1["faulty_servers"] == 4

        # 4. Query server detail
        detail1 = client.get("/api/v1/servers/dev/server-1").json()
        assert detail1["is_faulty"] is True
        assert any(f["fault_id"] == "F101" for f in detail1["active_faults"])

        # 5. Resolve fault via manual file edit (appending RESOLVED entry)
        with open(server1_log, "a", encoding="utf-8") as f:
            f.write("2026-10-31 15:05:00 INFO RESOLVED id=F101\n")

        # 6. Rescan and verify fault is cleared
        client.post("/api/v1/scan", headers=AUTH_HEADERS)
        detail2 = client.get("/api/v1/servers/dev/server-1").json()
        assert detail2["is_faulty"] is False
        assert len(detail2["active_faults"]) == 0

        # Faulty servers count back down to 3
        rep2 = client.get("/api/v1/report").json()
        assert rep2["faulty_servers"] == 3


def test_e2e_log_rotation_handling(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=work_org,
        state_file=state_file,
        api_key=API_KEY,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        # Initial scan
        client.post("/api/v1/scan", headers=AUTH_HEADERS)

        # Truncate dev/server-2.log to 0 bytes and write a new fault
        server2_log = work_org / "dev" / "server-2.log"
        server2_log.write_text(
            "2026-10-31 16:00:00 CRIT [id=F999] rotated fresh catastrophic failure\n",
            encoding="utf-8",
        )

        # Rescan should detect truncation/rotation, reset offset, and pick up F999
        res = client.post("/api/v1/scan", headers=AUTH_HEADERS)
        assert res.status_code == 200

        detail = client.get("/api/v1/servers/dev/server-2").json()
        assert detail["is_faulty"] is True
        active_ids = {f["fault_id"] for f in detail["active_faults"]}
        assert "F999" in active_ids


def test_e2e_state_persistence_across_app_restart(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=work_org,
        state_file=state_file,
        api_key=API_KEY,
    )

    # First app run: resolve F001 on dev/server-2
    app1 = create_app(settings)
    with TestClient(app1) as client:
        client.post("/api/v1/scan", headers=AUTH_HEADERS)
        res = client.post(
            "/api/v1/servers/dev/server-2/faults/F001/resolve",
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200

    # Ensure state file was written
    assert state_file.exists()

    # Second app run from persisted state
    app2 = create_app(settings)
    with TestClient(app2) as client2:
        rep = client2.get("/api/v1/report").json()
        # Should still be 2 faulty servers because F001 remains resolved
        assert rep["faulty_servers"] == 2
        detail = client2.get("/api/v1/servers/dev/server-2").json()
        assert detail["is_faulty"] is False
