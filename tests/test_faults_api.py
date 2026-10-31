import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings


def test_faults_endpoint_listing_and_filters(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        # All active faults
        resp = client.get("/api/v1/faults")
        assert resp.status_code == 200
        faults = resp.json()
        assert len(faults) == 4

        # CRIT faults should be ordered first
        assert faults[0]["severity"] == "CRIT"
        assert faults[1]["severity"] == "CRIT"

        fault_ids = {f["fault_id"] for f in faults}
        assert fault_ids == {"F001", "F020", "F021", "F002"}

        # Filter by environment
        prd_faults = client.get("/api/v1/faults?env=prd").json()
        assert len(prd_faults) == 2
        assert {f["fault_id"] for f in prd_faults} == {"F020", "F021"}

        # Filter by min_severity
        crit_faults = client.get("/api/v1/faults?min_severity=CRIT").json()
        assert len(crit_faults) == 2
        assert all(f["severity"] == "CRIT" for f in crit_faults)

        # Invalid min_severity
        invalid_resp = client.get("/api/v1/faults?min_severity=NOT_REAL")
        assert invalid_resp.status_code == 422
