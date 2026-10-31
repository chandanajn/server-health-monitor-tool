import shutil
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.service import LogNotWritableError, append_resolution
from health_api.settings import Settings

API_KEY = "test-secret-key-16-chars"
AUTH_HEADERS = {"X-API-Key": API_KEY}


def test_append_resolution_helper(tmp_path: Path) -> None:
    test_file = tmp_path / "test.log"

    # Case 1: file without trailing newline
    test_file.write_bytes(b"2026-10-08 09:00:00 ERROR [id=F001] Crash")
    now = datetime(2026, 10, 8, 9, 10, 0)
    append_resolution(test_file, "F001", now=now)

    content = test_file.read_text(encoding="utf-8")
    assert content.endswith("\n2026-10-08 09:10:00 INFO RESOLVED id=F001\n")

    # Case 2: file with trailing newline
    append_resolution(test_file, "F002", now=now)
    content2 = test_file.read_text(encoding="utf-8")
    assert content2.endswith("2026-10-08 09:10:00 INFO RESOLVED id=F002\n")


def test_append_resolution_unwritable_raises(tmp_path: Path) -> None:
    nonexistent_dir_file = tmp_path / "nonexistent_dir" / "test.log"
    with pytest.raises(LogNotWritableError):
        append_resolution(nonexistent_dir_file, "F001")


def test_manual_scan_endpoint(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file, api_key=API_KEY)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.post("/api/v1/scan", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_servers"] == 6
        assert data["faulty_servers"] == 3


def test_resolve_fault_lifecycle(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file, api_key=API_KEY)
    app = create_app(settings)

    with TestClient(app) as client:
        # Resolve F001 on dev/server-2
        url = "/api/v1/servers/dev/server-2/faults/F001/resolve"
        resp = client.post(url, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["is_faulty"] is False
        assert len(detail["active_faults"]) == 0

        # Check log file actually contains the line
        log_content = (work_org / "dev" / "server-2.log").read_text(encoding="utf-8")
        assert "INFO RESOLVED id=F001\n" in log_content

        # Report should now show 2 faulty servers instead of 3
        rep = client.get("/api/v1/report").json()
        assert rep["faulty_servers"] == 2

        # Second resolve attempt gives 409 fault_not_active
        resp_409 = client.post(url, headers=AUTH_HEADERS)
        assert resp_409.status_code == 409
        assert "fault_not_active" in resp_409.json()["detail"]


def test_resolve_unknown_server_or_fault(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file, api_key=API_KEY)
    app = create_app(settings)

    with TestClient(app) as client:
        # Unknown server -> 404
        resp = client.post(
            "/api/v1/servers/dev/unknown_server/faults/F001/resolve",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 404

        # Unknown fault on valid server -> 404
        resp2 = client.post(
            "/api/v1/servers/dev/server-2/faults/F999/resolve",
            headers=AUTH_HEADERS,
        )
        assert resp2.status_code == 404


def test_resolve_path_safety_validation(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file, api_key=API_KEY)
    app = create_app(settings)

    with TestClient(app) as client:
        # ID exceeds 64 characters -> 422
        long_id = "A" * 65
        resp = client.post(
            f"/api/v1/servers/dev/server-2/faults/{long_id}/resolve",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 422


def test_resolve_multiple_faults_same_server(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file, api_key=API_KEY)
    app = create_app(settings)

    with TestClient(app) as client:
        # Resolve F020 on prd/server-6
        resp1 = client.post(
            "/api/v1/servers/prd/server-6/faults/F020/resolve",
            headers=AUTH_HEADERS,
        )
        assert resp1.status_code == 200
        assert len(resp1.json()["active_faults"]) == 1  # F021 still active

        # Resolve F021 on prd/server-6
        resp2 = client.post(
            "/api/v1/servers/prd/server-6/faults/F021/resolve",
            headers=AUTH_HEADERS,
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_faulty"] is False
        assert len(resp2.json()["active_faults"]) == 0

        # Check both lines landed in file
        content = (work_org / "prd" / "server-6.log").read_text(encoding="utf-8")
        assert "INFO RESOLVED id=F020\n" in content
        assert "INFO RESOLVED id=F021\n" in content

