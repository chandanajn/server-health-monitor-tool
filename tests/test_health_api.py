from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from health_api.main import create_app
from health_api.settings import Settings


def test_healthz_and_readyz(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server-1.log").write_text("2026-10-08 09:00:00 INFO Up\n", encoding="utf-8")
    state_file = tmp_path / "state.json"

    settings = Settings(root=log_dir, state_file=state_file, interval=30.0)
    app = create_app(settings)

    with TestClient(app) as client:
        # Liveness
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Readiness after startup scan
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}

        # Simulate staleness
        service = app.state.service
        service._last_success_at = datetime.now(UTC) - timedelta(seconds=120)

        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert "stale" in resp.json()["reason"]


def test_shutdown_persists_state(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server-1.log").write_text(
        "2026-10-08 09:00:00 ERROR [id=F001] Crash\n", encoding="utf-8"
    )
    state_file = tmp_path / "state.json"

    settings = Settings(root=log_dir, state_file=state_file)
    app = create_app(settings)

    with TestClient(app):
        pass  # Startup ran, shutdown runs upon exit

    assert state_file.exists()


def test_docs_disabled(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    state_file = tmp_path / "state.json"

    settings = Settings(root=log_dir, state_file=state_file, enable_docs=False)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/docs")
        assert resp.status_code == 404

