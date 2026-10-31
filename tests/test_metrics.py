import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings


def test_metrics_endpoint_reflects_scan_state(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=work_org,
        state_file=state_file,
        enable_metrics=True,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        body = resp.text

        # Gauge metrics for servers
        assert "hm_servers_total 6.0" in body
        assert "hm_servers_faulty 3.0" in body

        # Histogram for scan duration
        assert "hm_scan_duration_seconds_count" in body

        # Counters exist
        assert "hm_scan_failures_total" in body
        assert "hm_faults_resolved_total" in body


def test_metrics_fault_resolved_increments(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"
    api_key = "test-secret-key-for-metrics-test"

    settings = Settings(
        root=work_org,
        state_file=state_file,
        api_key=api_key,
        enable_metrics=True,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        initial_metrics = client.get("/metrics").text
        assert "hm_servers_faulty 3.0" in initial_metrics

        # Resolve F001 on server-2
        resolve_resp = client.post(
            "/api/v1/servers/dev/server-2/faults/F001/resolve",
            headers={"X-API-Key": api_key},
        )
        assert resolve_resp.status_code == 200

        after_metrics = client.get("/metrics").text
        # Faulty count dropped from 3 to 2
        assert "hm_servers_faulty 2.0" in after_metrics


def test_metrics_disabled_returns_404(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=work_org,
        state_file=state_file,
        enable_metrics=False,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "not_found"
