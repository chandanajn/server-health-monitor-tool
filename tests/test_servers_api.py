import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings


def test_servers_pagination_and_filters(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        # All servers
        resp = client.get("/api/v1/servers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6
        assert len(data["items"]) == 6

        # Pagination limit and offset
        page1 = client.get("/api/v1/servers?limit=2&offset=0").json()
        assert len(page1["items"]) == 2
        assert page1["items"][0]["server"] == "dev/server-1.log"

        page2 = client.get("/api/v1/servers?limit=2&offset=2").json()
        assert len(page2["items"]) == 2
        assert page2["items"][0]["server"] != page1["items"][0]["server"]

        # Filter by status
        faulty = client.get("/api/v1/servers?status=faulty").json()
        assert faulty["total"] == 3
        assert all(s["is_faulty"] for s in faulty["items"])

        healthy = client.get("/api/v1/servers?status=healthy").json()
        assert healthy["total"] == 3
        assert all(not s["is_faulty"] for s in healthy["items"])

        # Filter by environment
        prd = client.get("/api/v1/servers?env=prd").json()
        assert prd["total"] == 2
        assert all(s["env"] == "prd" for s in prd["items"])


def test_servers_validation_errors(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        # Invalid status
        resp = client.get("/api/v1/servers?status=bogus")
        assert resp.status_code == 422

        # Invalid limit
        resp = client.get("/api/v1/servers?limit=0")
        assert resp.status_code == 422
        resp = client.get("/api/v1/servers?limit=201")
        assert resp.status_code == 422


def test_server_detail_endpoint(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        # Valid server detail by stem
        resp = client.get("/api/v1/servers/prd/server-6")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["server"] == "prd/server-6.log"
        assert detail["env"] == "prd"
        assert detail["name"] == "server-6"
        assert detail["is_faulty"] is True
        assert detail["highest_severity"] == "CRIT"
        assert len(detail["active_faults"]) == 2

        # Valid with .log suffix
        resp_log = client.get("/api/v1/servers/prd/server-6.log")
        assert resp_log.status_code == 200
        assert resp_log.json()["name"] == "server-6"

        # Unknown server
        resp_404 = client.get("/api/v1/servers/prd/nonexistent")
        assert resp_404.status_code == 404

        # Unknown env
        resp_env_404 = client.get("/api/v1/servers/unknown_env/server-6")
        assert resp_env_404.status_code == 404

