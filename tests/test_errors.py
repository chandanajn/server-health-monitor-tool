import shutil
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from health_api.errors import AppError
from health_api.main import create_app
from health_api.settings import Settings


def test_error_envelope_404_not_found(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        # Route not found
        resp = client.get("/api/v1/nonexistent-endpoint")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "not_found"
        assert len(data["error"]["request_id"]) > 0
        assert resp.headers.get("X-Request-ID") == data["error"]["request_id"]

        # Resource not found (specific server)
        resp_srv = client.get("/api/v1/servers/prd/nonexistent-server")
        assert resp_srv.status_code == 404
        data_srv = resp_srv.json()
        assert data_srv["error"]["code"] == "server_not_found"
        assert "not found" in data_srv["error"]["message"]


def test_error_envelope_405_method_not_allowed(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        # POST to GET-only endpoint
        resp = client.post("/api/v1/servers")
        assert resp.status_code == 405
        data = resp.json()
        assert data["error"]["code"] == "method_not_allowed"
        assert "Method Not Allowed" in data["error"]["message"]


def test_error_envelope_422_validation_error(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.get("/api/v1/servers?limit=-1")
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "validation_error"
        assert "error" in data


def test_request_id_reused_if_supplied(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    custom_id = "test-custom-request-id-42"
    with TestClient(app) as client:
        # Success response echoes request id
        resp = client.get("/healthz", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_id

        # Error response echoes custom request id
        resp_err = client.get(
            "/api/v1/servers/prd/ghost",
            headers={"X-Request-ID": custom_id},
        )
        assert resp_err.status_code == 404
        assert resp_err.headers.get("X-Request-ID") == custom_id
        assert resp_err.json()["error"]["request_id"] == custom_id


def test_custom_app_error(tmp_path: Path) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    test_router = APIRouter()

    @test_router.get("/trigger-app-error")
    def trigger_app_error() -> None:
        raise AppError(code="custom_code", message="custom error description", status_code=400)

    app.include_router(test_router)

    with TestClient(app) as client:
        resp = client.get("/trigger-app-error")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "custom_code"
        assert data["error"]["message"] == "custom error description"


def test_unhandled_500_does_not_leak_internals(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    work_org = tmp_path / "org"
    shutil.copytree("org", work_org)
    state_file = tmp_path / "state.json"

    settings = Settings(root=work_org, state_file=state_file)
    app = create_app(settings)

    test_router = APIRouter()

    @test_router.get("/trigger-crash")
    def trigger_crash() -> None:
        raise RuntimeError("super secret database password leaked in exception trace")

    app.include_router(test_router)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/trigger-crash", headers={"X-Request-ID": "crash-req-001"})
        assert resp.status_code == 500
        data = resp.json()
        assert data["error"]["code"] == "internal_error"
        assert data["error"]["message"] == "An unexpected internal server error occurred"
        assert data["error"]["request_id"] == "crash-req-001"
        assert "super secret" not in resp.text
