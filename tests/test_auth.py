from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings


def test_auth_no_key_configured_writes_disabled(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    state_file = tmp_path / "state.json"

    # api_key is None by default
    settings = Settings(root=log_dir, state_file=state_file, api_key=None)
    app = create_app(settings)

    with TestClient(app) as client:
        # GET is public
        resp = client.get("/api/v1/report")
        assert resp.status_code == 200

        # POST without key configured returns 403 writes_disabled
        post_resp = client.post("/api/v1/scan")
        assert post_resp.status_code == 403
        assert "writes_disabled" in post_resp.json()["detail"]


def test_auth_missing_or_wrong_api_key(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    state_file = tmp_path / "state.json"

    settings = Settings(
        root=log_dir,
        state_file=state_file,
        api_key="correct-secret-key-16-chars",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        # Missing key header -> 401
        resp = client.post("/api/v1/scan")
        assert resp.status_code == 401
        assert "missing API key" in resp.json()["detail"]

        # Wrong key header -> 401
        resp = client.post(
            "/api/v1/scan",
            headers={"X-API-Key": "wrong-secret-key-at-least-16-chars"},
        )
        assert resp.status_code == 401
        assert "invalid API key" in resp.json()["detail"]

        # Correct key header -> 200
        resp = client.post(
            "/api/v1/scan",
            headers={"X-API-Key": "correct-secret-key-16-chars"},
        )
        assert resp.status_code == 200

